#! /bin/bash
#
# Flush the nat/mangle OUTPUT chains that hold the uplink routing rules, keeping
# the bandwidth-limit mangle rules. Normally called from within
# prevent_location_issue.sh (which holds the routing lock); when invoked
# standalone (wepn-run 1 8) it takes the same lock so it cannot run in the
# middle of a rebuild.

LOCKDIR=/run/wepn
LOCKFILE="$LOCKDIR/routing.lock"
if [ -z "${WEPN_ROUTING_LOCKED:-}" ]; then
	mkdir -p "$LOCKDIR" 2>/dev/null
	chown root:root "$LOCKDIR" 2>/dev/null
	chmod 0755 "$LOCKDIR" 2>/dev/null
	touch "$LOCKFILE" 2>/dev/null
	chown root:root "$LOCKFILE" 2>/dev/null
	chmod 0600 "$LOCKFILE" 2>/dev/null
	# -w matches prevent_location_issue.sh: above the worst-case rebuild time,
	# so a queued flush is not silently dropped while a slow rebuild runs.
	env WEPN_ROUTING_LOCKED=1 flock -w 180 -E 66 "$LOCKFILE" "$0" "$@"
	rc=$?
	if [ "$rc" -eq 66 ]; then
		echo "routing lock busy; skipping flush"
		logger -t wepn-routing "flush skipped: routing lock busy for 180s" 2>/dev/null
		exit 0
	fi
	exit "$rc"
fi

IPT="iptables -w 5"
IP6T="ip6tables -w 5"

# fetch existing bandwidth limits so we can restore them after the flush
BW_LINES=`iptables-save 2>/dev/null | grep "wepn-bw"`

$IPT -t nat -F
$IP6T -t nat -F
# Collapse to exactly one copy. Older versions of this script did a bare -A on
# every run (many times a day for months), so live Pods have accumulated dozens
# of identical rules in the filter OUTPUT chain, which -F above does not touch.
while $IPT -D OUTPUT -p icmp -j REJECT 2>/dev/null; do :; done
$IPT -A OUTPUT -p icmp -j REJECT
# needed for wireguard
while $IPT -t nat -D POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null; do :; done
$IPT -t nat -A POSTROUTING -o eth0 -j MASQUERADE
# empty mangle routes
$IPT -t mangle -F PREROUTING
$IPT -t mangle -F OUTPUT
# v6 mangle OUTPUT holds the same uplink mark/carve-out rules (added by
# forward_all_traffic) but was historically never flushed — one copy of every
# rule accumulated per all-traffic rebuild, and a block-quic/uplink change
# never removed the old v6 rules. No other component writes v6 mangle OUTPUT
# (wireguard uses filter FORWARD + nat POSTROUTING; the bandwidth limiter and
# its restore below are v4-only), so a full flush is safe.
$IP6T -t mangle -F OUTPUT
# forward_all_traffic adds this to mangle POSTROUTING (not flushed above); drop
# every copy here so it cannot accumulate across all-traffic rebuilds, and so a
# switch away from all-traffic actually removes it.
while $IPT -t mangle -D POSTROUTING -o wg0 -j MARK --set-xmark 0x30/0xffffffff 2>/dev/null; do :; done

# now restore bandwidth limiting lines ($line is intentionally unquoted: it is a
# full rule spec from iptables-save; see docs/routing-security-debt.md SD-5)
echo "$BW_LINES" | while IFS= read -r line; do
  [ -z "$line" ] && continue
  echo "Processing: $line"
  $IPT -t mangle $line
done
