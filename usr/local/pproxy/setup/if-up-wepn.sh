#!/bin/bash
# Re-apply iptables routing rules when a physical interface comes up.
# Excludes virtual interfaces (wg*, tun*, etc.) to avoid loops on VPN restarts.
case "$IFACE" in
    eth0|wlan0)
        /bin/bash /usr/local/sbin/prevent_location_issue.sh
        ;;
esac
