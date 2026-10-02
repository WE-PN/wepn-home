#!/bin/bash
ROOT=/usr/local/sbin/

cd $ROOT
# Re-apply the uplink routing rules (tor redirects for go.we-pn.com/wrong-location,
# or full WARP/Tor routing). prevent_location_issue.sh flushes and rebuilds
# atomically under its own flock, so there is no need for a separate pre-flush
# (wepn-run 1 8) here — that only widened the window where no rules were present.
/usr/local/sbin/wepn-run 1 9

# block access to local ip addresses by remote connections
# /usr/local/sbin/wepn-run 1 10
