#!/bin/bash

echo -e "Reloading and restarting services"
export SYSTEMD_PAGER=""

sleep 10
SYSTEMCTL="/usr/bin/systemctl --no-block --no-pager"

$SYSTEMCTL daemon-reload 
$SYSTEMCTL enable wepn-api
echo "step 1"
$SYSTEMCTL enable wepn-keypad
$SYSTEMCTL enable wepn-leds
$SYSTEMCTL enable wepn-poweroff
echo "step 2"
$SYSTEMCTL enable wepn-main
$SYSTEMCTL enable wepn-mqtt
$SYSTEMCTL enable wepn-messages

# pproxy has moved to wepn-main on systemctl
if test -f /etc/rc3.d/S01pproxy; then
  /bin/sh /etc/init.d/pproxy restart
  update-rc.d pproxy disable
fi

$SYSTEMCTL restart wepn-leds
$SYSTEMCTL restart wepn-keypad
$SYSTEMCTL restart wepn-metrics
$SYSTEMCTL restart wepn-api
$SYSTEMCTL restart shadowsocks-libev
$SYSTEMCTL restart shadowsocks-libev-manager
$SYSTEMCTL restart wepn-main
# restart the producers after wepn-main: the old main process may have held
# the broker connection with the same MQTT client-id, so the forwarder goes last
$SYSTEMCTL restart wepn-mqtt
$SYSTEMCTL restart wepn-messages

echo "Restarts done"
