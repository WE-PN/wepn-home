#! /bin/bash
#
# fetch existing bandwidth limits

BW_LINES=`iptables-save | grep "wepn-bw"`

iptables -t nat -F
ip6tables -t nat -F
iptables -A OUTPUT -p icmp -j REJECT
# needed for wireguard
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
# empty mangle routes
sudo iptables -t mangle -F PREROUTING
sudo iptables -t mangle -F OUTPUT

# now restore bandwidth limiting lines
echo "$BW_LINES" | while IFS= read -r line; do
  echo "Processing: $line"
  iptables -t mangle $line
done
