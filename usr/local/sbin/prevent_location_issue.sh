#!/bin/bash

# If remote VPN users use certain (unknown) service, their location data
# is used to mark this device's IP address. As a result, this IP might 
# get incorrectly marked as part of a different country.
# To prevent, we periodically get the list of servers who make this mistake,
# and traffic to them is routed through Tor or WARP.
# Both Tor and WARP IPs are already known to providers as VPN, so they
# should not see an impact and know this traffic is proxies.

# See https://go.we-pn.com/wrong-location

get_conf_value() {
	filter=$1
	default=$2
	V=`cat /etc/pproxy/config.ini  | grep $filter |tr -d ' ' | awk -F"=" '{print \$2}'`
	ret=${V:=$default}
	return $ret
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

get_conf_value "orport" "8991"
ORPORT=$ret
get_conf_value "transport" "9040"
TRANSPORT=$ret
# redproxy creates a socks proxy for WARP
get_conf_value "redproxy-port" "8999"
REDPORT=$ret

# chooses which mode to use for "uplink" of select traffic that can cause IP geo issues
# this one comes from status file
UPLINK=`cat /var/local/pproxy/status.ini  | grep uplink | tr -d ' ' | awk -F"=" '{print $2}'`
UPLINK=${UPLINK:=tor}

USER=pproxy

if [ $UPLINK = "tor" ]
then
	DEST_PORT=$TRANSPORT
else
	DEST_PORT=$REDPORT
fi

# we don't want to redirect traffic if Tor is not active
# TODO: need to replace this with flags. What if Tor fails?
# Leaving it to block traffic if Tor not active. Otherwise
# pod owner's IP gets blocked by you-know-who
#if ! [[ $(netstat -tulpn | grep LISTEN | grep $ORPORT) ]]; then
#	echo "Tor not running, no need to redirect traffic"
#	exit
#fi

wget -4 -T10 https://www.gstatic.com/ipranges/goog.txt -O goog.txt
wget -4 -T10 https://www.gstatic.com/ipranges/cloud.json -O cloud.json
iptables -t nat -F
ip6tables -t nat -F


# google ones

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

