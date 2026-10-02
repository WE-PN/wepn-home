#!/bin/bash
# Re-apply iptables routing rules when a physical interface comes up.
# Excludes virtual interfaces (wg*, tun*, etc.) to avoid loops on VPN restarts.
#
# Runs in the ifupdown/NetworkManager hook context, which fires the instant the
# link is up — before the default route and DNS are usable. Running
# prevent_location_issue.sh synchronously here would resolve we-pn.com against a
# not-yet-ready resolver and could install a degraded ruleset. Instead we
# background a short readiness wait; the rebuild itself is serialised by the
# flock inside prevent_location_issue.sh.
case "$IFACE" in
    eth0|wlan0)
        mkdir -p /run/wepn 2>/dev/null
        # skip if a rebuild is running right now (note: this check releases the
        # lock immediately — it does NOT dedupe waiters; that is the job of the
        # ifup-wait lock held inside the subshell below)
        flock -n /run/wepn/routing.lock true 2>/dev/null || exit 0
        (
            # hold a dedicated waiter lock for this subshell's whole lifetime:
            # a flapping link fires this hook repeatedly, and without a lock
            # held *across* the readiness wait every invocation would queue its
            # own rebuild. Only one waiter may exist at a time.
            exec 9>>/run/wepn/ifup-wait.lock
            flock -n 9 || exit 0
            chmod 0600 /run/wepn/ifup-wait.lock 2>/dev/null
            for i in $(seq 1 30); do
                if ip route | grep -q '^default' && getent hosts we-pn.com >/dev/null 2>&1; then
                    break
                fi
                sleep 2
            done
            /bin/bash /usr/local/sbin/prevent_location_issue.sh
        ) </dev/null >/dev/null 2>&1 &
        ;;
esac
