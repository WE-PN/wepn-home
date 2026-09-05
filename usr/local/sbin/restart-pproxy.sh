/bin/systemctl restart wepn-api
/bin/systemctl restart wepn-keypad
# wepn-mqtt/wepn-messages must restart only after wepn-main comes back up
# (restarting them first causes an MQTT client-id kick loop against the
# still-running old wepn-main during upgrades, see 6c5cbb3). But this
# script is itself a descendant of wepn-main.service, which has
# KillMode=control-group: restarting wepn-main here would SIGTERM this
# script's whole process group - including the rest of this file - before
# the next lines run. Hand the ordered sequence to a detached transient
# unit outside wepn-main's cgroup so it survives that teardown.
/bin/systemd-run --collect --quiet /bin/sh -c '
    /bin/systemctl restart wepn-main
    /bin/systemctl restart wepn-mqtt
    /bin/systemctl restart wepn-messages
'
