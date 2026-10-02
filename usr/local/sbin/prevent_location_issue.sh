#!/bin/bash
#
# Re-applies the Pod's uplink routing rules (direct / Tor / WARP) based on
# /var/local/pproxy/networking.ini (falling back to /etc/pproxy/config.ini).
#
# This script is invoked from many uncoordinated triggers (@reboot, a 12h cron,
# every eth0/wlan0 up event, and the networking/tor services). It flushes and
# rebuilds the nat/mangle OUTPUT chains, so concurrent runs would corrupt the
# ruleset. All runs are therefore serialised through a single flock, and the
# rebuild is designed to fail *closed* (route through the uplink) rather than
# silently fall back to direct routing.

set -o noglob

MARK=0x30

current_user=`whoami`
if [[ $current_user != "root" ]]; then
	echo "Need to run this script as root"
	exit 1
fi

##############################################################################
# Serialise every rebuild through one flock. The lock lives in /run/wepn, a
# root-owned dir we manage ourselves:
#   - NOT /run/lock (world-writable) — a local account could hold the lock and
#     block routing repair.
#   - NOT /run/pproxy — that is a systemd RuntimeDirectory= shared by wepn-main,
#     wepn-api, wepn-mqtt, ... ; systemd chowns it to the service user on every
#     start and deletes it on stop, so it cannot be a stable root-only dir.
# iptables-flush.sh honours the same lock / env guard.
##############################################################################
LOCKDIR=/run/wepn
LOCKFILE="$LOCKDIR/routing.lock"
if [ -z "${WEPN_ROUTING_LOCKED:-}" ]; then
	mkdir -p "$LOCKDIR" 2>/dev/null
	chown root:root "$LOCKDIR" 2>/dev/null
	chmod 0755 "$LOCKDIR" 2>/dev/null
	# 0600 root:root: only root may even open the lock file, so an unprivileged
	# local account cannot acquire it (flock on an O_RDONLY fd would otherwise
	# still take an exclusive lock) and grief the rebuild.
	touch "$LOCKFILE" 2>/dev/null
	chown root:root "$LOCKFILE" 2>/dev/null
	chmod 0600 "$LOCKFILE" 2>/dev/null
	# -w must stay above the worst-case holder duration (warp branch: up to
	# ~120s of warp-cli timeouts + DNS retries + geo wgets), or a trigger
	# carrying a *newer* networking.ini can time out and its config is silently
	# dropped until the next 12h cron.
	env WEPN_ROUTING_LOCKED=1 flock -w 180 -E 66 "$LOCKFILE" "$0" "$@"
	rc=$?
	if [ "$rc" -eq 66 ]; then
		echo "routing rebuild already in progress; skipping this run"
		logger -t wepn-routing "rebuild skipped: routing lock busy for 180s" 2>/dev/null
		exit 0
	fi
	exit "$rc"
fi

# bounded iptables invocations: wait briefly for the xtables lock instead of
# failing outright, but never block forever.
IPT="iptables -w 5"
IP6T="ip6tables -w 5"

get_conf_value() {
	filter=$1
	default=$2
	file=$3
	V=$(grep -E "^[[:space:]]*${filter}[[:space:]]*=" "$file" 2>/dev/null \
		| head -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//')
	ret=${V:=$default}
}

# accepts a bare IPv4/IPv6 address or a CIDR; rejects anything with whitespace
# or iptables option characters (defence against argument injection when the
# value originates from a file or a resolver). The IPv6 branch requires at
# least one ':' — colon-less hex like "abcd" would otherwise be dispatched to
# the IPv4/iptables path, where iptables treats it as a hostname and does a
# DNS lookup as root. Prefix lengths are bounded (v4 <=32, v6 <=128).
is_valid_ip_or_cidr() {
	local x="$1"
	[[ "$x" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}(/(3[0-2]|[12]?[0-9]))?$ ]] && return 0
	[[ "$x" =~ ^[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4})+(/(12[0-8]|1[01][0-9]|[1-9]?[0-9]))?$ ]] && return 0
	return 1
}

# A backend carve-out must name a specific host/network, never a blanket range:
# config.ini is pproxy-writable, so an overly-broad CIDR here would silently
# RETURN all traffic past the catch-all mark and disable the uplink (see
# docs/routing-security-debt.md SD-7). v4 must be /8 or narrower, v6 /32 or
# narrower.
is_valid_carveout() {
	local x="$1" plen
	is_valid_ip_or_cidr "$x" || return 1
	case "$x" in 0.0.0.0*|::|::/*) return 1 ;; esac
	if [[ "$x" == */* ]]; then
		plen=${x##*/}
		if [[ "$x" == *:* ]]; then
			[ "$plen" -ge 32 ] || return 1
		else
			[ "$plen" -ge 8 ] || return 1
		fi
	fi
	return 0
}

# TCP/UDP port, 1-65535. --to-ports must never receive junk: a failed REDIRECT
# with the catch-all MARK still installed means marked traffic egresses direct.
is_valid_port() {
	[[ "$1" =~ ^[0-9]{1,5}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

do_geo_iptables() {
	ip=$1
	if [ -z "$ip" ]; then
		return
	fi
	if ! is_valid_ip_or_cidr "$ip"; then
		echo "skipping malformed geo entry: $ip"
		return
	fi

	for proto in tcp udp; do
		if [[ ! $ip == *:* ]]; then
			$IPT -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner "$USER" --dst "$ip" -m $proto -j REDIRECT --to-ports "$DEST_PORT"
		else
			$IP6T -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner "$USER" --dst "$ip" -m $proto -j REDIRECT --to-ports "$DEST_PORT"
		fi
	done
}

BACKEND_HOST="we-pn.com"
BACKEND_CACHE_V4="$LOCKDIR/backend-ip.v4"
BACKEND_CACHE_V6="$LOCKDIR/backend-ip.v6"

# Prints the validated backend IPs (one per line) that must be excluded from the
# uplink so the Pod's own heartbeat/config/OTA traffic is not tunnelled.
# Sources, in order: operator-pinned CIDRs in config.ini (width-capped by
# is_valid_carveout — config.ini is pproxy-writable, so a blanket CIDR must
# never be able to disable the uplink), then a retried live DNS lookup, then
# the last cached good answer (cache lives under the root-only $LOCKDIR).
resolve_backend_ips() {
	local out="" ip v4="" v6="" i

	get_conf_value "backend-cidrs" "" "$pproxy_config_file"
	if [ -n "$ret" ]; then
		for ip in ${ret//,/ }; do
			if is_valid_carveout "$ip"; then
				out+="$ip"$'\n'
			else
				echo "ignoring malformed or overly broad backend-cidr: $ip"
				logger -t wepn-routing "ignoring backend-cidr: $ip" 2>/dev/null
			fi
		done
	fi

	for i in 1 2 3; do
		v4=$(timeout 5 host -t A "$BACKEND_HOST" 2>/dev/null | awk '/has address/ {print $4}' | head -1)
		[ -n "$v4" ] && break
		sleep 2
	done
	v6=$(timeout 5 host -t AAAA "$BACKEND_HOST" 2>/dev/null | awk '/has IPv6 address/ {print $5}' | head -1)

	if is_valid_ip_or_cidr "$v4"; then
		echo "$v4" > "$BACKEND_CACHE_V4"
	else
		v4=$(cat "$BACKEND_CACHE_V4" 2>/dev/null)
	fi
	if is_valid_ip_or_cidr "$v6"; then
		echo "$v6" > "$BACKEND_CACHE_V6"
	else
		v6=$(cat "$BACKEND_CACHE_V6" 2>/dev/null)
	fi

	is_valid_ip_or_cidr "$v4" && out+="$v4"$'\n'
	is_valid_ip_or_cidr "$v6" && out+="$v6"$'\n'
	printf '%s' "$out"
}

forward_all_traffic() {
	local proto dport bip backends protos
	backends=$(resolve_backend_ips)
	if [ -z "$backends" ]; then
		echo "WARNING: backend IPs unknown; routing ALL matching traffic through the uplink (fail-closed)"
	fi

	# UDP (QUIC/HTTP-3) cannot be handed to a TCP proxy. With block-quic on
	# (default) we mark it and REJECT it so clients fall back to TCP, which *is*
	# tunnelled — nothing leaks. With block-quic off we leave UDP alone: QUIC
	# then egresses directly (better throughput, but the destination sees the
	# Pod's real IP for those flows).
	if [ "$BLOCK_QUIC" = "1" ]; then
		protos="tcp udp"
	else
		protos="tcp"
	fi

	# idempotent: iptables-flush.sh only flushes mangle PREROUTING/OUTPUT, not
	# POSTROUTING, so a bare -A here would pile up one copy per all-traffic rebuild.
	while $IPT -t mangle -D POSTROUTING -o wg0 -j MARK --set-xmark $MARK 2>/dev/null; do :; done
	$IPT -t mangle -A POSTROUTING -o wg0 -j MARK --set-xmark $MARK

	for proto in $protos; do
		for dport in 80 443; do
			# carve out the Pod's own backend traffic BEFORE the catch-all mark,
			# so a failed lookup just means "tunnel everything" (fail-closed).
			while IFS= read -r bip; do
				[ -z "$bip" ] && continue
				if [[ "$bip" == *:* ]]; then
					$IP6T -t mangle -A OUTPUT ! -o lo -d "$bip" -p $proto -m $proto --dport $dport -m owner --uid-owner "$USER" -j RETURN
				else
					$IPT -t mangle -A OUTPUT ! -o lo -d "$bip" -p $proto -m $proto --dport $dport -m owner --uid-owner "$USER" -j RETURN
				fi
			done <<< "$backends"

			$IPT -t mangle -A OUTPUT ! -o lo -p $proto -m $proto --dport $dport -m owner --uid-owner "$USER" -j MARK --set-mark $MARK
			$IP6T -t mangle -A OUTPUT ! -o lo -p $proto -m $proto --dport $dport -m owner --uid-owner "$USER" -j MARK --set-mark $MARK
		done
	done

	$IPT -t nat -A OUTPUT -p tcp -m mark --mark $MARK -j REDIRECT --to-ports "$DEST_PORT"
	# IPv6: redsocks/tor bind 127.0.0.1, so marked v6 TCP cannot be REDIRECTed
	# into the proxy — and without the nat step the mark alone does nothing,
	# meaning marked v6 TCP would egress DIRECT (a location leak on any
	# IPv6-enabled LAN). Fail closed: reset it so getaddrinfo/Happy-Eyeballs
	# falls back to IPv4, which *is* tunnelled. Backend carve-outs RETURN
	# before the mark, so the Pod's own v6 backend traffic is unaffected.
	$IP6T -A WEPN_UPLINK -p tcp -m mark --mark $MARK -j REJECT --reject-with tcp-reset
	if [ "$BLOCK_QUIC" = "1" ]; then
		$IPT -A WEPN_UPLINK -p udp -m mark --mark $MARK -j REJECT --reject-with icmp-port-unreachable
		$IP6T -A WEPN_UPLINK -p udp -m mark --mark $MARK -j REJECT --reject-with icmp6-port-unreachable
	fi
}

w="warp-cli --accept-tos"
pproxy_config_file="/etc/pproxy/config.ini"
pproxy_status_file="/var/local/pproxy/status.ini"
pproxy_networking_file="/var/local/pproxy/networking.ini"
GEO_DIR=/var/local/pproxy/geo


get_conf_value "uplink" "" $pproxy_networking_file
UPLINK=${ret,,}
if [ -z "$UPLINK" ]; then
	get_conf_value "uplink" "tor" $pproxy_config_file
	UPLINK=${ret,,}
fi

# Mode of operation for uplink
get_conf_value "routing-mode" "" $pproxy_networking_file
UPLINK_MODE=${ret,,}
if [ -z "$UPLINK_MODE" ]; then
	get_conf_value "routing-mode" "geo" $pproxy_config_file
	UPLINK_MODE=${ret,,}
fi

# "direct" uplink means "no uplink" — there is nowhere to route to, so any
# routing-mode other than none is contradictory. Force none rather than install
# REDIRECT rules that point at port 0.
if [ "$UPLINK" = "direct" ] && [ "$UPLINK_MODE" != "none" ]; then
	echo "uplink=direct: forcing routing-mode from '$UPLINK_MODE' to 'none'"
	UPLINK_MODE=none
fi

# block-quic: in all-traffic mode, reject pproxy-owned UDP 80/443 (QUIC) so
# clients fall back to tunnelled TCP instead of leaking direct. Default on.
get_conf_value "block-quic" "" $pproxy_networking_file
block_quic_raw=${ret,,}
if [ -z "$block_quic_raw" ]; then
	get_conf_value "block-quic" "true" $pproxy_config_file
	block_quic_raw=${ret,,}
fi
case "$block_quic_raw" in
	false|0|no|off) BLOCK_QUIC=0 ;;
	*)              BLOCK_QUIC=1 ;;
esac

get_conf_value "warp-port" "8971" $pproxy_config_file
warp_port=$ret
if ! is_valid_port "$warp_port"; then
	echo "invalid warp-port '$warp_port', using 8971"
	warp_port=8971
fi

# redproxy creates a socks proxy for WARP
get_conf_value "redproxy-port" "8999" $pproxy_config_file
REDPORT=$ret
if ! is_valid_port "$REDPORT"; then
	echo "invalid redproxy-port '$REDPORT', using 8999"
	REDPORT=8999
fi

get_conf_value "transport" "9040" $pproxy_config_file
TRANSPORT=$ret
if ! is_valid_port "$TRANSPORT"; then
	echo "invalid transport port '$TRANSPORT', using 9040"
	TRANSPORT=9040
fi

USER=pproxy
echo $UPLINK

case $UPLINK in
	"direct")
		need_warp=0
		DEST_PORT=0
		;;

	"tor")
		need_warp=0
		DEST_PORT=$TRANSPORT
		;;

	"warp")
		need_warp=1
		DEST_PORT=$REDPORT
		warp_status_output=$(timeout 15 warp-cli --accept-tos status)
		if echo "$warp_status_output" | grep -q "Status update: Connected"; then
			need_warp=0
		fi
		if [[ $need_warp == 1 ]]
		then
			echo "Starting up warp"
			timeout 15 $w registration new
			timeout 15 $w mode proxy
			timeout 15 $w connect

			timeout 15 $w disconnect
			timeout 15 $w mode proxy
			timeout 15 $w proxy port $warp_port
			timeout 15 $w connect
		fi
		# redsocks terminates the REDIRECT and forwards into WARP's proxy port;
		# if it is not running the redirect black-holes, so make sure it is up.
		if ! systemctl is-active --quiet redsocks 2>/dev/null; then
			echo "redsocks not active, starting it"
			systemctl restart redsocks 2>/dev/null
		fi
		;;
	*)
		echo "Uplink unrecognized, will default to tor to be safe"
		need_warp=0
		DEST_PORT=$TRANSPORT
		;;
esac


# clear rules, except ones that need to be preserved (bw limit, etc.).
# We already hold the routing lock; the env guard stops iptables-flush.sh from
# trying to re-acquire it (which would deadlock).
/bin/bash /usr/local/sbin/iptables-flush.sh

# dedicated filter chain for uplink-related rejects, flushed and rebuilt each run
$IPT -N WEPN_UPLINK 2>/dev/null
$IPT -C OUTPUT -j WEPN_UPLINK 2>/dev/null || $IPT -A OUTPUT -j WEPN_UPLINK
$IPT -F WEPN_UPLINK
$IP6T -N WEPN_UPLINK 2>/dev/null
$IP6T -C OUTPUT -j WEPN_UPLINK 2>/dev/null || $IP6T -A OUTPUT -j WEPN_UPLINK
$IP6T -F WEPN_UPLINK

case $UPLINK_MODE in
	"geo")
		# route only select routes that cause below problem, not all of the traffic

		# If remote VPN users use certain (unknown) service, their location data
		# is used to mark this device's IP address. As a result, this IP might
		# get incorrectly marked as part of a different country.
		# To prevent, we periodically get the list of servers who make this mistake,
		# and traffic to them is routed through Tor or WARP.
		# Both Tor and WARP IPs are already known to providers as VPN, so they
		# should not see an impact and know this traffic is proxies.

		# See https://go.we-pn.com/wrong-location
		mkdir -p "$GEO_DIR"
		wget -4 -T10 https://www.gstatic.com/ipranges/goog.txt -O "$GEO_DIR/goog.txt"
		wget -4 -T10 https://www.gstatic.com/ipranges/cloud.json -O "$GEO_DIR/cloud.json"

		# goog.txt and cloud.json overlap heavily; merge + sort -u so a CIDR that
		# appears in both does not get a duplicate REDIRECT rule. Every entry is
		# still validated by do_geo_iptables() before use.
		{
			grep -v '8\.8\.' "$GEO_DIR/goog.txt" 2>/dev/null
			jq -r '.prefixes[] | (.ipv4Prefix // empty), (.ipv6Prefix // empty)' \
				"$GEO_DIR/cloud.json" 2>/dev/null
		} | tr -d ' ' | grep -v '^$' | sort -u | while read -r ip; do
			do_geo_iptables "$ip"
		done
		;;
	"none")
		echo "No uplink path selected, direct routing enabled"
		;;
	"all-traffic"|"all")
		# all traffic is routed
		# this is helpful for when someone is just using their IP as entry point,
		# but exits are through Tor/WARP
		echo "All traffic needs to route through $UPLINK"
		forward_all_traffic
		;;
	*)
		echo "Uplink mode unrecognized, will default to routing all traffic to be safe"
		echo "All traffic needs to route through $UPLINK"
		forward_all_traffic
		;;
esac
