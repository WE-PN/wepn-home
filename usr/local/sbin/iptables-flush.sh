#! /bin/bash
iptables -t nat -F
ip6tables -t nat -F
iptables -A OUTPUT -p icmp -j REJECT
# needed for wireguard
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
