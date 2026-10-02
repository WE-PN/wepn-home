# Uplink routing: rebuild hardening — status and follow-ups

Date: 2026-08-29 · Branch: dev
(merges the former routing-fix-handoff.md, routing-security-debt.md,
routing-uplink-testplan.md)

## 1. What was done

**Problem:** with `networking.ini` set to `uplink = warp`, `routing-mode =
all-traffic`, the Pod worked initially but after some time silently went back to
routing traffic directly — a runtime iptables problem, not a config problem.

**Root causes:** (1) `Networking.start()`/`Tor.start()` fired the bare flush
(`1 8`) and the rebuild (`1 9`) both detached — when the flush landed after the
rebuild, the rules were wiped for up to 12h; (2) nothing serialised the many
triggers (@reboot, 12h cron, every if-up, service starts), and the catch-all
MARK was skipped entirely when `host we-pn.com` failed (exactly what happens at
interface-up) → all traffic direct; (3) `/run/pproxy` (systemd
`RuntimeDirectory=`, chowned/deleted per service) could not host a root-only
lock, hence `/run/wepn`.

**The fix (all in this change set):**

- **Serialisation:** every rebuild/flush runs under one flock on
  `/run/wepn/routing.lock` (`root:root 0600` — non-root cannot even open it;
  `flock -w 180 -E 66`, skips are logged to syslog via `logger -t
  wepn-routing`). `iptables-flush.sh` shares the lock; `WEPN_ROUTING_LOCKED=1`
  skips re-locking when called from inside the rebuild. `/run/wepn` is created
  by `post-install.sh` + a tmpfiles.d entry (and defensively by the scripts).
- **Race removed:** `Networking.start()` / `Tor.start()` fire only `1 9`
  (self-flushing); the 12h cron dropped its pre-flush.
- **Fail-closed:** the catch-all MARK is always installed; the backend
  carve-out is `RETURN` rules *before* it, fed from validated, width-capped
  `[networking] backend-cidrs` → retried DNS → root-only cache. Failed lookup =
  tunnel everything, never direct.
- **IPv6 fail-closed:** marked v6 TCP is `REJECT --reject-with tcp-reset` in
  ip6tables `WEPN_UPLINK` (no v6 nat REDIRECT is possible — redsocks/tor bind
  127.0.0.1), so clients fall back to tunnelled v4 instead of leaking direct.
- **QUIC:** nat REDIRECT is TCP-only; with `[networking] block-quic` (default
  `true`) pproxy-owned UDP 80/443 is marked and REJECTed in `WEPN_UPLINK` →
  clients fall back to tunnelled TCP. `false` leaves UDP alone (QUIC direct).
- **if-up hook:** backgrounds a readiness wait (default route + DNS) instead of
  a synchronous rebuild; a dedicated `ifup-wait.lock` held across the wait
  dedupes waiters under link flapping.
- **Hardening:** `set -o noglob`; `iptables -w 5`; `is_valid_ip_or_cidr` /
  `is_valid_carveout` / `is_valid_port` validation on every value reaching root
  iptables; anchored `get_conf_value` parsing; `timeout` on `warp-cli`/`host`;
  redsocks liveness check; `uplink=direct` forces `routing-mode=none`.
- **Accumulation bugs fixed:** duplicate `-p icmp REJECT` / MASQUERADE / wg0
  POSTROUTING-mark collapse to one copy; geo lists merged through `sort -u`;
  and `ip6tables -t mangle -F OUTPUT` added to the flush — the v6 mark rules
  were historically **never** flushed and accumulated one copy per rebuild.

A critical review pass then found and fixed seven further issues (F1 v6 TCP
fail-open, F2 lock wait below worst-case holder, F3 if-up waiter stacking, F4
blanket `backend-cidrs` kill switch, F5 unvalidated ports breaking fail-closed,
F6 loose v6 validation regex, F7 the never-flushed v6 mangle chain). All are in
the shipped code above.

**Verified:** 523 unit tests pass (incl. `test_networking.py` start/configure
coverage); the on-Pod runner (`sudo tests/runners/routing_uplink_test.py all`)
passes fully on the dev Pod — uplink×mode matrix, R1 restart race, R2
concurrent rebuilds, R3 DNS-failure fail-closed, R5 held/stale lock, R6 cron
path, R7 root-only lock, R8 malformed cache + broad-CIDR rejection, R9 flap
storm (held and unheld lock), R10 block-quic toggle.

**Files:** `usr/local/sbin/prevent_location_issue.sh`,
`usr/local/sbin/iptables-flush.sh`, `networking.py`, `tor.py`,
`periodic/iptables-actions.sh`, `setup/if-up-wepn.sh`, `setup/post-install.sh`,
`setup/update_config.py`, `tests/units/test_networking.py`,
`tests/runners/routing_uplink_test.py`.

## 2. How to test

The sbin scripts and if-up hook are real copied files (the rest is live via the
`/usr/local/pproxy` symlink); on a dev Pod redeploy before testing:

```
sudo install -o root -g root -m 755 usr/local/sbin/prevent_location_issue.sh /usr/local/sbin/
sudo install -o root -g root -m 755 usr/local/sbin/iptables-flush.sh          /usr/local/sbin/
sudo install -o root -g root -m 755 usr/local/pproxy/setup/if-up-wepn.sh /etc/network/if-up.d/wepn-iptables
sudo wepn-run 1 1

wepn-env/bin/python tests/run_tests_with_coverage.py      # unit, any machine
sudo tests/runners/routing_uplink_test.py all             # Pod only; stops wepn-main, snapshots/restores everything
```

Soak: 24h spanning a 12h cron + a DHCP renew; hourly
`sudo -u pproxy curl -s https://ifconfig.io` must stay on the uplink egress IP.

## 3. Open items

Threat model for the security items: a local, lower-privileged account
(`pproxy`, `wepn-api`, or a compromised service) escalating to root, or
defeating the uplink so traffic egresses directly — de-anonymising every
connected VPN user.

### Security

- **SD-1 (High) — `wepn-run` setuid-root has no authorization.** Group
  `wepn-web` = {`pproxy`, `wepn-api`}; the LAN-facing local API user can invoke
  *any* of the ~32 setuid commands (poweroff, raw iptables flush, apt install,
  stop any service, add/remove WG peers, mount disks). One RCE in the local API
  = near-total root control. Fix: partition the command table by calling
  uid/gid (or split binaries); audit what the local API actually needs — if
  nothing, drop `wepn-api` from `wepn-web`. Also scrub/allowlist the
  environment in `setuid.c`: a caller can currently pre-set
  `WEPN_ROUTING_LOCKED=1` to bypass the routing lock (serialization only, no
  privilege gain).
- **SD-2 (High) — geo-mode root parses pproxy-writable files.** The geo branch
  wgets `goog.txt`/`cloud.json` into pproxy-writable `/var/local/pproxy/geo`,
  then iterates them into root iptables. `is_valid_ip_or_cidr` now blocks
  argument injection, but the validation is the only barrier. Fix: fetch into
  root-only `/run/wepn/geo`, drop privileges for the wget, fetch to a temp file
  and `mv` on success (`wget -O` currently truncates on failure → empty geo set
  until the next run), consider `ipset` instead of thousands of `-A` rules.
- **SD-3 (Medium) — backend carve-out trusts the resolver.** A hostile resolver
  steers which IP is excluded from tunnelling (validated + cached, so no
  injection). Fix: ship a pinned WEPN-infra `backend-cidrs` default so DNS is
  fallback-only; have `heartbeat.py` cross-check the effective carve-out.
- **SD-4 (Medium) — `/etc/pproxy/config.ini` is world-readable** and
  pproxy-writable, and holds key material / SMTP / DDNS tokens. Fix: `0640`,
  split secrets out; check every consumer after tightening.
- **SD-7 (Medium) — no detection when the uplink is defeated.** A compromised
  `wepn-web` member can flush rules or stop services with no alarm. (The
  quieter `backend-cidrs = 0.0.0.0/0` variant is now blocked by the carve-out
  width cap.) Fix: `heartbeat.py`/`diag.py` compare configured uplink vs.
  effective iptables state (`WEPN_UPLINK` chain, mark→REDIRECT rule, egress
  probe) and raise a device error code on mismatch; make raw flush
  reachable only through the locked rebuild path.
- **SD-5 (Low) — bandwidth-limit restore re-execs `iptables-save` lines
  unquoted** (root-generated input, low risk). Fix: rebuild from
  `limit_bandwidth.sh` per active user, or filtered save/restore.
- **SD-8 (Low) — carve-outs are `RETURN` rules directly in mangle OUTPUT**, so
  matching packets skip all later mangle OUTPUT rules. Safe today
  (order-dependent); fix: a dedicated `WEPN_MARK` mangle chain mirroring
  `WEPN_UPLINK`.

### Bugs / functional

- **v6 nat asymmetry:** the flush runs `ip6tables -t nat -F` (wiping
  WireGuard's v6 `POSTROUTING MASQUERADE`) but re-adds only the **v4** eth0
  MASQUERADE — WG v6 client NAT is lost after every rebuild until the next
  `PostUp`.
- **Geo-mode UDP** (SD-6 remainder): `do_geo_iptables` still REDIRECTs UDP to
  the TCP proxy port (black-holes QUIC for geo destinations). Apply the same
  TCP-only + REJECT treatment as all-traffic.

### Test coverage gaps

- R4 (real interface flap → deferred rebuild) is documented, not automated.
- The geo-list rejection path isn't in the runner (the branch re-wgets the list
  every run, so a poisoned line can't be staged); only bash-unit-checked.
- No coverage for: OTA/backend traffic actually bypassing the uplink
  (carve-out), WireGuard/OpenVPN user traffic *not* matching the
  `--uid-owner pproxy` rules (negative test), a real v6 egress probe.
- `tests/regression/`: add an assertion that effective egress matches the
  configured uplink after `wepn-run 1 1`.
