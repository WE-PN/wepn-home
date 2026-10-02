#!/usr/bin/env python3
"""
Informal on-Pod validation for uplink routing (direct / Tor / WARP).

NOT a unit test: no mocks, needs root, mutates iptables / networking.ini /
resolv.conf, and only makes sense on a real dev Pod. It snapshots everything it
touches and restores on exit (including Ctrl-C / SIGTERM).

    sudo tests/runners/routing_uplink_test.py matrix        # uplink x mode grid
    sudo tests/runners/routing_uplink_test.py regression    # R1..R9 (the revert bug)
    sudo tests/runners/routing_uplink_test.py once --uplink warp --mode all-traffic
    sudo tests/runners/routing_uplink_test.py all

Background: docs/routing-uplink-testplan.md
"""

import argparse
import atexit
import os
import shutil
import signal
import subprocess  # nosec: fixed argv lists, go.we-pn.com/waiver-1
import sys
import time

UP_DIR = os.path.dirname(os.path.abspath(__file__)) + "/../../"
sys.path.append(UP_DIR)

NETWORKING_INI = "/var/local/pproxy/networking.ini"
RESOLV_CONF = "/etc/resolv.conf"
LOCKFILE = "/run/wepn/routing.lock"
PREVENT = "/usr/local/sbin/prevent_location_issue.sh"
WEPN_RUN = "/usr/local/sbin/wepn-run"
SNAP_DIR = "/run/wepn/routing-test-snapshot"

MODES = ["none", "geo", "all-traffic"]
UPLINKS = ["direct", "tor", "warp"]

_passed = 0
_failed = 0
_reference_direct_ip = None


# --------------------------------------------------------------------------- #
# shell helpers
# --------------------------------------------------------------------------- #
def run(argv, timeout=30, check=False):
    """Run argv (list), return (rc, stdout+stderr)."""
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # nosec
                       timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    if check and p.returncode != 0:
        raise RuntimeError("cmd failed (%d): %s\n%s" % (p.returncode, " ".join(argv), out))
    return p.returncode, out


def iptables_save(v6=False):
    _, out = run(["ip6tables-save" if v6 else "iptables-save"])
    return out


def egress_ip(user="pproxy", http3=False, max_time=15, tries=1):
    argv = ["sudo", "-u", user, "curl", "-s", "--max-time", str(max_time)]
    if http3:
        argv += ["--http3-only"]
    argv += ["https://ifconfig.io"]
    for _ in range(tries):
        try:
            rc, out = run(argv, timeout=max_time + 10)
        except subprocess.TimeoutExpired:
            continue
        out = out.strip()
        if rc == 0 and out and len(out) < 60:
            return out
    return None


def proxy_port_listening(port):
    _, out = run(["ss", "-lntH"])
    return any((":%s " % port) in ln or ln.rstrip().endswith(":%s" % port)
               for ln in out.splitlines())


def dest_port_for(uplink):
    """the local port forward_all_traffic REDIRECTs TCP to, per uplink"""
    return {"warp": 8999, "tor": 9040}.get(uplink)


def classify_egress(ip):
    if ip is None:
        return "blackhole"
    if _reference_direct_ip and ip == _reference_direct_ip:
        return "direct"
    return "proxied"          # warp or tor - good enough for these checks


def lock_is_free(timeout=90):
    """Wait until nobody holds the routing lock (flock -n succeeds)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, _ = run(["flock", "-n", LOCKFILE, "true"])
        if rc == 0:
            return True
        time.sleep(1)
    return False


# --------------------------------------------------------------------------- #
# assertions
# --------------------------------------------------------------------------- #
def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  PASS  " + msg)
    else:
        _failed += 1
        print("  FAIL  " + msg)
    return cond


def has_redirect_rule():
    """all-traffic mode: mark 0x30 -> REDIRECT."""
    out = iptables_save()
    return any("REDIRECT" in ln and "--to-ports" in ln and "0x30" in ln
               for ln in out.splitlines())


def has_v6_tcp_reject():
    """all-traffic mode: marked v6 TCP must be REJECTed (tcp-reset) in
    WEPN_UPLINK — there is no v6 nat REDIRECT (redsocks/tor bind 127.0.0.1),
    so a v6 mangle MARK without this reject means marked v6 TCP egresses
    direct: the fail-open leak fixed in review finding F1."""
    out = iptables_save(v6=True)
    return any("WEPN_UPLINK" in ln and "-p tcp" in ln and "REJECT" in ln
               and "0x30" in ln for ln in out.splitlines())


def v6_no_orphan_tcp_mark():
    """no v6 TCP MARK rule may exist unless the WEPN_UPLINK reject backs it."""
    out = iptables_save(v6=True)
    marks = any("-p tcp" in ln and "MARK" in ln and "0x30" in ln
                for ln in out.splitlines())
    return (not marks) or has_v6_tcp_reject()


def has_geo_redirect():
    """geo mode: per-destination REDIRECT (no mark)."""
    out = iptables_save()
    return any("REDIRECT" in ln and "--to-ports" in ln
               and ("--dst " in ln or " -d " in ln) and "0x30" not in ln
               for ln in out.splitlines())


def count_catchall_mark():
    out = iptables_save()
    return sum(1 for ln in out.splitlines()
               if ln.startswith("-A OUTPUT") and "0x30" in ln
               and "MARK" in ln and " -d " not in ln)


def _dupe_multiset():
    """{rule_line: extra_copies_beyond_the_first} for the current ruleset,
    v4 AND v6 (the v6 blind spot hid the never-flushed ip6tables mangle OUTPUT
    accumulation that R10 exposed). Keys are prefixed per family so an
    identical rule text in both tables is not miscounted as a duplicate."""
    counts = {}
    for v6 in (False, True):
        for ln in iptables_save(v6=v6).splitlines():
            if ln.startswith("-A "):
                key = ("v6 " if v6 else "v4 ") + ln
                counts[key] = counts.get(key, 0) + 1
    return {k: n - 1 for k, n in counts.items() if n > 1}


# duplicates that already existed before the test started (historical cruft from
# the old iptables-flush.sh); we only fail on NEW duplication.
_baseline_dupes = {}


def new_duplicate_rules():
    cur = _dupe_multiset()
    return {ln: n for ln, n in cur.items() if n > _baseline_dupes.get(ln, 0)}


# --------------------------------------------------------------------------- #
# environment control
# --------------------------------------------------------------------------- #
def write_networking_ini(uplink, mode, block_quic=None):
    with open(NETWORKING_INI, "w") as fh:
        fh.write("[networking]\nenabled = True\nuplink = %s\nrouting-mode = %s\n"
                 % (uplink, mode))
        if block_quic is not None:
            fh.write("block-quic = %s\n" % block_quic)


def apply(uplink, mode, wait=True, block_quic=None):
    write_networking_ini(uplink, mode, block_quic)
    run([WEPN_RUN, "1", "9"], timeout=120)
    if wait:
        lock_is_free()
        time.sleep(2)


def rebuild_only(wait=True):
    run([WEPN_RUN, "1", "9"], timeout=120)
    if wait:
        lock_is_free()
        time.sleep(2)


# --------------------------------------------------------------------------- #
# snapshot / restore
# --------------------------------------------------------------------------- #
def snapshot():
    os.makedirs(SNAP_DIR, exist_ok=True)
    os.chmod(SNAP_DIR, 0o700)
    if os.path.exists(NETWORKING_INI):
        shutil.copy2(NETWORKING_INI, SNAP_DIR + "/networking.ini")
    if os.path.exists(RESOLV_CONF):
        shutil.copy2(RESOLV_CONF, SNAP_DIR + "/resolv.conf")
    with open(SNAP_DIR + "/iptables.rules", "w") as fh:
        fh.write(iptables_save())
    with open(SNAP_DIR + "/ip6tables.rules", "w") as fh:
        fh.write(iptables_save(v6=True))
    print("snapshot saved to " + SNAP_DIR)


_restored = False
_stopped_main = False


def stop_pproxy():
    """pproxy's Networking service reconciles networking.ini against the backend
    config and rebuilds on every startup / MQTT config push, which races the
    test. Stop it for the duration."""
    global _stopped_main
    rc, _ = run(["systemctl", "is-active", "--quiet", "wepn-main"])
    if rc == 0:
        run(["systemctl", "stop", "wepn-main"], timeout=60)
        _stopped_main = True
        print("stopped wepn-main for the test")
        time.sleep(2)


def start_pproxy():
    if _stopped_main:
        run(["systemctl", "start", "wepn-main"], timeout=60)


def restore():
    global _restored
    if _restored:
        return
    _restored = True
    print("\nrestoring pre-test state ...")
    try:
        if os.path.exists(SNAP_DIR + "/resolv.conf"):
            shutil.copy2(SNAP_DIR + "/resolv.conf", RESOLV_CONF)
        if os.path.exists(SNAP_DIR + "/networking.ini"):
            shutil.copy2(SNAP_DIR + "/networking.ini", NETWORKING_INI)
        if os.path.exists(SNAP_DIR + "/iptables.rules"):
            with open(SNAP_DIR + "/iptables.rules") as fh:
                subprocess.run(["iptables-restore"], stdin=fh, timeout=30)  # nosec
        if os.path.exists(SNAP_DIR + "/ip6tables.rules"):
            with open(SNAP_DIR + "/ip6tables.rules") as fh:
                subprocess.run(["ip6tables-restore"], stdin=fh, timeout=30)  # nosec
        # re-run the real rebuild so the Pod ends in its configured state
        run([PREVENT], timeout=120)
    except Exception as exc:                       # noqa - best effort
        print("  restore hit an error: %s" % exc)
        print("  MANUAL: check %s and re-run %s" % (NETWORKING_INI, PREVENT))
    start_pproxy()
    print("done (wepn-main restarted)." if _stopped_main else "done.")


def _sig(*_a):
    restore()
    sys.exit(1)


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def preflight(force_dev):
    if os.geteuid() != 0:
        sys.exit("must run as root")
    is_dev = force_dev
    try:
        from constants import LOG_CONFIG
        is_dev = is_dev or "debug" in LOG_CONFIG
    except Exception:
        pass
    if not is_dev:
        sys.exit("refusing to run: not a dev Pod (pass --dev-pod to override)")
    for tool in ("iptables-save", "flock", "curl"):
        if shutil.which(tool) is None:
            sys.exit("missing required tool: " + tool)


def capture_reference_ip():
    global _reference_direct_ip, _baseline_dupes
    # snapshot already saved; put us on direct routing to read the real egress IP.
    # This also runs one full rebuild, so the ruleset is in a known state before
    # we record the pre-existing duplicate cruft to diff against later.
    apply("direct", "none")
    _reference_direct_ip = egress_ip()
    _baseline_dupes = _dupe_multiset()
    print("reference direct egress IP: %s" % _reference_direct_ip)
    if _baseline_dupes:
        print("  note: %d pre-existing duplicated rule(s) ignored as baseline"
              % len(_baseline_dupes))
    if _reference_direct_ip is None:
        print("  WARNING: could not read direct egress IP; egress checks degraded")


# --------------------------------------------------------------------------- #
# test bodies
# --------------------------------------------------------------------------- #
def test_matrix():
    for uplink in UPLINKS:
        for mode in MODES:
            tag = "%s/%s" % (uplink, mode)
            print("\n[matrix] uplink=%s mode=%s" % (uplink, mode))
            apply(uplink, mode)

            if uplink == "direct":
                # direct uplink => no routing regardless of mode
                check(not has_redirect_rule() and not has_geo_redirect(),
                      "no REDIRECT rules for %s" % tag)
                check(classify_egress(egress_ip()) == "direct",
                      "egress is direct for %s" % tag)
            elif mode == "all-traffic":
                # what this test is really about: the routing rules are installed.
                check(has_redirect_rule(), "mark->REDIRECT present for %s" % tag)
                check(count_catchall_mark() >= 1, "catch-all MARK present for %s" % tag)
                check(has_v6_tcp_reject(),
                      "marked v6 TCP is REJECTed (fail-closed) for %s" % tag)
                check(v6_no_orphan_tcp_mark(),
                      "no orphan v6 TCP MARK (would egress direct) for %s" % tag)
                # whether the proxy behind the REDIRECT actually carries traffic
                # is that proxy's problem, not the routing script's. Hard-check it
                # only for warp (the reference uplink on this Pod); tor is
                # best-effort (a warp-configured Pod usually has no live circuit).
                port = dest_port_for(uplink)
                cls = classify_egress(egress_ip(max_time=40, tries=2))
                if uplink == "warp":
                    check(cls == "proxied",
                          "egress proxied for %s (got %s)" % (tag, cls))
                elif cls == "proxied":
                    print("  PASS  egress proxied for %s" % tag)
                else:
                    print("  INFO  %s egress=%s — routing rules are correct; the "
                          "proxy on tcp/%s is not carrying traffic on this Pod" %
                          (tag, cls, port))
            elif mode == "geo":
                # reliable invariants: geo never installs the all-traffic
                # catch-all, and untargeted traffic still goes out direct.
                check(not has_redirect_rule(),
                      "no catch-all mark->REDIRECT for %s (geo is per-dest)" % tag)
                check(classify_egress(egress_ip()) == "direct",
                      "general egress stays direct for %s" % tag)
                # best-effort: the per-destination redirects require the gstatic
                # IP lists to have downloaded this run.
                if has_geo_redirect():
                    print("  PASS  per-destination REDIRECT present for %s" % tag)
                else:
                    print("  SKIP  no per-dest REDIRECT for %s "
                          "(gstatic geo list fetch likely failed this run)" % tag)
            else:  # none
                check(not has_redirect_rule() and not has_geo_redirect(),
                      "no REDIRECT rules for %s" % tag)

            dupes = new_duplicate_rules()
            check(not dupes,
                  "no new duplicate iptables rules after %s%s"
                  % (tag, "" if not dupes else " (%s)" % list(dupes)[:2]))


def read_uplink():
    _, out = run(["cat", NETWORKING_INI])
    for ln in out.splitlines():
        if ln.strip().startswith("uplink"):
            return ln.split("=", 1)[1].strip().lower()
    return "?"


def test_regression():
    # baseline: the scenario the bug was reported against
    print("\n[R0] baseline warp/all-traffic")
    apply("warp", "all-traffic")
    check(has_redirect_rule() and count_catchall_mark() >= 1,
          "baseline ruleset installed")
    base_cls = classify_egress(egress_ip(max_time=40, tries=2))
    check(base_cls == "proxied", "baseline egress proxied (got %s)" % base_cls)

    # R1 is the original bug: Networking.start() fired 1 8 + 1 9 detached, and
    # the bare flush could land after the rebuild -> direct routing. start()
    # runs on every pproxy boot, so: restart pproxy repeatedly, confirm the
    # routing rules are never left missing. pproxy's fetch_config rewrites
    # networking.ini from the backend on boot, so assert against whatever the
    # uplink ends up being, not a fixed value.
    print("\n[R1] restart race x8 (pproxy up for this test only)")
    ok = True
    checked = 0
    for i in range(8):
        run(["systemctl", "restart", "wepn-main"], timeout=120)
        time.sleep(12)
        lock_is_free()
        up = read_uplink()
        if up in ("tor", "warp"):
            checked += 1
            if not (has_redirect_rule() or has_geo_redirect()):
                ok = False
                print("    iteration %d: uplink=%s but NO routing rules (reverted to direct)" % (i, up))
                break
        else:
            print("    iteration %d: uplink=%s, skipping routing assertion" % (i, up))
    check(ok and checked > 0,
          "routing rules survive pproxy restarts (%d/%d iterations asserted)" % (checked, 8))
    stop_pproxy()   # back to a quiescent state for the remaining scenarios

    print("\n[R2] 5 concurrent rebuilds")
    procs = [subprocess.Popen([PREVENT], stdout=subprocess.PIPE,  # nosec
                              stderr=subprocess.STDOUT) for _ in range(5)]
    outs = []
    for p in procs:
        try:
            outs.append(p.communicate(timeout=180)[0].decode("utf-8", "replace"))
        except subprocess.TimeoutExpired:
            p.kill()
            outs.append("<timeout>")
    lock_is_free()
    check(not any("xtables lock" in o for o in outs),
          "no xtables-lock errors under concurrency")
    dupes = new_duplicate_rules()
    check(not dupes,
          "no new duplicate rules after concurrent rebuilds%s"
          % ("" if not dupes else " (%s)" % list(dupes)[:2]))
    check(has_redirect_rule(), "REDIRECT rule intact after concurrent rebuilds")

    print("\n[R3] DNS failure -> fail closed")
    apply("warp", "all-traffic")
    try:
        os.rename(RESOLV_CONF, RESOLV_CONF + ".rtest")
        rc, out = run([PREVENT], timeout=120)
        lock_is_free()
        check(has_redirect_rule() and count_catchall_mark() >= 1,
              "ruleset still installed with DNS broken")
        check(classify_egress(egress_ip(max_time=40, tries=2)) != "direct",
              "egress not direct with DNS broken (fail-closed)")
    finally:
        if os.path.exists(RESOLV_CONF + ".rtest"):
            os.rename(RESOLV_CONF + ".rtest", RESOLV_CONF)

    print("\n[R5] held lock -> serialise / skip, never corrupt")
    apply("warp", "all-traffic")

    # 5a: the if-up hook's guard (flock -n) must bail immediately while held
    holder = subprocess.Popen(["flock", LOCKFILE, "sleep", "6"])  # nosec
    time.sleep(0.5)
    rc, _ = run(["flock", "-n", LOCKFILE, "true"], timeout=5)
    check(rc != 0, "flock -n bails immediately while lock held")
    holder.wait()

    # 5b: a direct invocation waits for the lock (-w 60) then rebuilds; it must
    #     not skip for a short hold, and the ruleset stays intact throughout.
    holder = subprocess.Popen(["flock", LOCKFILE, "sleep", "6"])  # nosec
    time.sleep(0.5)
    t0 = time.time()
    run([PREVENT], timeout=90)
    waited = time.time() - t0
    check(waited >= 4, "direct rebuild waited for the held lock (%.1fs)" % waited)
    check(has_redirect_rule(), "ruleset intact after contended rebuild")
    holder.wait()

    # 5c: a leftover lock file with no holder does not block anything
    run([PREVENT], timeout=90)
    check(has_redirect_rule(), "stale lock file does not block a rebuild")

    print("\n[R6] periodic/iptables-actions.sh path")
    rc, out = run(["/bin/bash",
                   UP_DIR + "periodic/iptables-actions.sh"], timeout=180)
    lock_is_free()
    check(has_redirect_rule(), "cron path leaves REDIRECT rule installed")

    print("\n[R7] routing lock is root-only")
    rc, _ = run(["sudo", "-u", "pproxy", "flock", "-w", "1", LOCKFILE, "true"])
    check(rc != 0, "pproxy cannot open/take the routing lock")
    st = os.stat("/run/wepn")
    check(st.st_uid == 0 and (st.st_mode & 0o777) == 0o755,
          "/run/wepn is root:root 0755")
    lst = os.stat(LOCKFILE)
    check(lst.st_uid == 0 and (lst.st_mode & 0o777) == 0o600,
          "%s is root:root 0600" % LOCKFILE)

    print("\n[R8] malformed backend-ip cache is rejected")
    apply("warp", "all-traffic")

    def managed_rules():
        _, a = run(["iptables", "-w", "5", "-t", "mangle", "-S", "OUTPUT"])
        _, b = run(["iptables", "-w", "5", "-t", "nat", "-S", "OUTPUT"])
        return a + b

    # break DNS so resolve_backend_ips() actually falls back to the cache file
    dns_broken = False
    try:
        os.rename(RESOLV_CONF, RESOLV_CONF + ".r8")
        dns_broken = True
        for payload in ("1.2.3.4 -j ACCEPT", "$(reboot)", "9.9.9.9 -j DROP"):
            with open("/run/wepn/backend-ip.v4", "w") as fh:
                fh.write(payload + "\n")
            run([PREVENT], timeout=120)
            lock_is_free()
            r = managed_rules()
            check("1.2.3.4" not in r and "9.9.9.9" not in r
                  and "-j ACCEPT" not in r and "-j DROP" not in r,
                  "payload %r did not reach the routing rules" % payload[:18])
    finally:
        if dns_broken and os.path.exists(RESOLV_CONF + ".r8"):
            os.rename(RESOLV_CONF + ".r8", RESOLV_CONF)
    # (the geo-list validation path — is_valid_ip_or_cidr in do_geo_iptables — is
    # covered directly by a bash unit check, not here: the geo branch wgets and
    # overwrites goog.txt on every run, so an injected line cannot be staged.)

    # width cap (review F4): blanket carve-outs from pproxy-writable config.ini
    # must never install — a RETURN matching everything (iptables -S renders a
    # 0.0.0.0/0 dst as no -d at all) or a /4 would silently disable the uplink.
    cfg = "/etc/pproxy/config.ini"
    with open(cfg) as fh:
        cfg_orig = fh.read()
    try:
        with open(cfg, "a") as fh:
            fh.write("\nbackend-cidrs = 0.0.0.0/0, 128.0.0.0/4\n")
        run([PREVENT], timeout=120)
        lock_is_free()
        rules = managed_rules().splitlines()
        blanket = [ln for ln in rules if "-j RETURN" in ln and " -d " not in ln]
        broad = [ln for ln in rules if "-j RETURN" in ln and "128.0.0.0/4" in ln]
        check(not blanket and not broad,
              "overly broad backend-cidrs entries are rejected")
    finally:
        with open(cfg, "w") as fh:
            fh.write(cfg_orig)

    print("\n[R9] if-up flap storm")
    apply("warp", "all-traffic")
    hook = UP_DIR + "setup/if-up-wepn.sh"
    env = dict(os.environ, IFACE="eth0")
    # while a rebuild holds the lock, every hook invocation must bail (flock -n)
    holder = subprocess.Popen(["flock", LOCKFILE, "sleep", "12"])  # nosec
    time.sleep(0.3)
    for _ in range(10):
        subprocess.Popen(["/bin/bash", hook], env=env)  # nosec
        time.sleep(0.3)
    time.sleep(1)
    rc, out = run(["pgrep", "-fc", "prevent_location_issue"])
    n = int(out.strip() or "0")
    check(n == 0, "hooks bail while a rebuild holds the lock (spawned %d)" % n)
    holder.wait()
    lock_is_free(timeout=180)
    check(has_redirect_rule(), "ruleset intact after if-up storm")
    check(not new_duplicate_rules(), "no new duplicate rules after if-up storm")

    # unheld-lock flap (review F3): with no rebuild running, the ifup-wait lock
    # held inside the hook's backgrounded subshell must dedupe waiters — at most
    # one may survive a storm. Break DNS so the survivor actually sits in its
    # readiness loop instead of rebuilding immediately.
    os.rename(RESOLV_CONF, RESOLV_CONF + ".r9")
    try:
        for _ in range(10):
            subprocess.Popen(["/bin/bash", hook], env=env)  # nosec
            time.sleep(0.2)
        time.sleep(3)
        rc, out = run(["pgrep", "-fc", "if-up-wepn"])
        n = int(out.strip() or "0")
        check(n <= 1,
              "at most one readiness-waiter survives a flap storm (got %d)" % n)
    finally:
        run(["pkill", "-f", "if-up-wepn"])
        os.rename(RESOLV_CONF + ".r9", RESOLV_CONF)
    lock_is_free(timeout=180)

    print("\n[R10] block-quic=false leaves QUIC alone")
    apply("warp", "all-traffic", block_quic="false")
    both = iptables_save() + iptables_save(v6=True)
    check(not any("-p udp" in ln and "0x30" in ln for ln in both.splitlines()),
          "no UDP mark/REJECT rules with block-quic=false")
    check(has_redirect_rule(),
          "TCP mark->REDIRECT still installed with block-quic=false")
    check(has_v6_tcp_reject(),
          "v6 TCP reject still installed with block-quic=false")
    apply("warp", "all-traffic")
    both = iptables_save() + iptables_save(v6=True)
    check(any("-p udp" in ln and "0x30" in ln and "REJECT" in ln
              for ln in both.splitlines()),
          "UDP REJECT returns with block-quic default")


def test_once(uplink, mode):
    print("\n[once] uplink=%s mode=%s" % (uplink, mode))
    apply(uplink, mode)
    print("--- nat OUTPUT ---")
    _, o = run(["iptables", "-w", "5", "-t", "nat", "-S", "OUTPUT"])
    print(o)
    print("--- mangle OUTPUT ---")
    _, o = run(["iptables", "-w", "5", "-t", "mangle", "-S", "OUTPUT"])
    print(o)
    print("--- WEPN_UPLINK ---")
    _, o = run(["iptables", "-w", "5", "-S", "WEPN_UPLINK"])
    print(o)
    print("egress: %s (%s)" % (egress_ip(), classify_egress(egress_ip())))


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["matrix", "regression", "once", "all"])
    ap.add_argument("--uplink", choices=UPLINKS, default="warp")
    ap.add_argument("--mode", dest="rmode", choices=MODES, default="all-traffic")
    ap.add_argument("--dev-pod", action="store_true",
                    help="override the dev-Pod safety check")
    args = ap.parse_args()

    preflight(args.dev_pod)
    snapshot()
    atexit.register(restore)
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    stop_pproxy()
    capture_reference_ip()

    if args.mode in ("matrix", "all"):
        test_matrix()
    if args.mode in ("regression", "all"):
        test_regression()
    if args.mode == "once":
        test_once(args.uplink, args.rmode)

    print("\n=========================================")
    print("PASSED: %d   FAILED: %d" % (_passed, _failed))
    print("=========================================")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
