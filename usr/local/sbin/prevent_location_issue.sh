#!/bin/bash

current_user=`whoami`
if [[ $current_user != "root" ]]; then
	echo "Need to run this script as root"
	exit
fi
get_conf_value() {
	filter=$1
	default=$2
	file=$3
	V=`cat $file  | grep $filter |tr -d ' ' | awk -F"=" '{print \$2}'`
	ret=${V:=$default}
}
do_iptables() {
	ip=$1
	if [ -z "$ip" ]; then
		exit
	fi

	for proto in tcp udp; do
		if [[ ! $ip == *:* ]]; then
			iptables -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner $USER --dst $ip -m $proto -j REDIRECT --to-ports $DEST_PORT
		else
			ip6tables -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner $USER --dst $ip -m $proto -j REDIRECT --to-ports $DEST_PORT

		fi
	done
}
w="warp-cli --accept-tos"
pproxy_config_file="/etc/pproxy/config.ini"
pproxy_status_file="/var/local/pproxy/status.ini"

get_conf_value "uplink" "tor" $pproxy_status_file
UPLINK=$ret

# Mode of operation for uplink
get_conf_value "routing-mode" "none" $pproxy_status_file
UPLINK_MODE=$ret

get_conf_value "warp-port" "8971" $pproxy_config_file
warp_port=$ret

# redproxy creates a socks proxy for WARP
get_conf_value "redproxy-port" "8999" $pproxy_config_file
REDPORT=$ret

get_conf_value "transport" "9040" $pproxy_config_file
TRANSPORT=$ret

USER=pproxy
echo $UPLINK
if [[ $UPLINK == "tor" ]]
then
	need_warp=0
	DEST_PORT=$TRANSPORT
fi

if [[ $UPLINK == "warp" ]]
then
	need_warp=1
	DEST_PORT=$REDPORT
	warp_status_output=$(warp-cli --accept-tos status)
	if echo "$warp_status_output" | grep -q "Status update: Connected"; then
		need_warp=0
	fi
	if [[ $need_warp == 1 ]]
	then
		echo "Starting up warp"
		$w registration new
		$w mode proxy
		$w connect

		$w disconnect
		$w mode proxy
		$w proxy port $warp_port
		$w connect
	fi
fi

# clear past rules in NAT
iptables -t nat -F
ip6tables -t nat -F

if [[ $UPLINK_MODE == "all-traffic" ]]
then
	# all traffic is routed
	# this is helpful for when someone is just using their IP as entry point,
	# but exits are through Tor/WARP
	echo "All traffic needs to route through WARP"
	for proto in tcp udp; do
		for dport in "80" "443"; do
			iptables -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner $USER --dport $dport -m $proto -j REDIRECT --to-ports $DEST_PORT
			ip6tables -t nat -A OUTPUT ! -o lo -p $proto -m owner --uid-owner $USER --dport $dport -m $proto -j REDIRECT --to-ports $DEST_PORT
		done
	done
else
	# route only select routes that cause below problem, not all of the traffic

	# If remote VPN users use certain (unknown) service, their location data
	# is used to mark this device's IP address. As a result, this IP might
	# get incorrectly marked as part of a different country.
	# To prevent, we periodically get the list of servers who make this mistake,
	# and traffic to them is routed through Tor or WARP.
	# Both Tor and WARP IPs are already known to providers as VPN, so they
	# should not see an impact and know this traffic is proxies.

	# See https://go.we-pn.com/wrong-location
	wget -4 -T10 https://www.gstatic.com/ipranges/goog.txt -O goog.txt
	wget -4 -T10 https://www.gstatic.com/ipranges/cloud.json -O cloud.json
	# google ones
	#
	# TODO: exteremly unlikely, but to be safe need to sanitize the incoming text files too

	for ip in `cat goog.txt | grep -v 8\.8\.`; do
		do_iptables $ip
	done

	echo "doing cloud now"

	# cloud ones
	jq -c ".${str}" cloud.json | while read ip; do
		do_iptables $ip
	done

	jq ".prefixes[].ipv4Prefix" cloud.json  -c --raw-output | grep -v null | while read ip; do
		do_iptables $ip
	done



	jq ".prefixes[].ipv6Prefix" cloud.json  -c --raw-output | grep -v null | while read ip; do
		do_iptables $ip
	done
fi
