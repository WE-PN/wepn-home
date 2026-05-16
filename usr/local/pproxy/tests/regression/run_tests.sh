status_file="/var/local/pproxy/status.ini"
pproxy_config="/etc/pproxy/config.ini"
source regenv/bin/activate
wepn-run 1 1
sleep 5
if grep -q CLAIMED "$status_file";
then
	echo "Device not unclaimed properly, doing so now"
	pytest wepn-regression.py  -vv -k 'test_login or test_unclaim'
	wepn-run 1 1
fi
while grep -q CLAIMED $status_file; do
	echo "still claimed, waiting ... ";
	sleep 5 ;
done
wepn-run 1 1

# Wait for pproxy to cycle through all cached previous keys (onboard-timeout=10s
# × 5 keys ≈ 50s) and settle on a freshly generated random key. 180s leaves ample
# margin. This eliminates the race where test_claim registers an old key while
# pproxy has already moved on to a different one.
echo "Waiting 180s for pproxy to settle on a fresh key..."
sleep 180

# Confirm temporary_key is ready (should be immediate after the settle period).
for i in $(seq 1 12); do
    tmp_key=$(python3 -c "
import configparser
c = configparser.ConfigParser()
c.read('$status_file')
try: print(c.get('status', 'temporary_key'))
except: print('')
" 2>/dev/null)
    if [ -n "$tmp_key" ] && [ "$tmp_key" != "CLAIMED" ]; then break; fi
    sleep 5
done
echo "Onboard key ready: $tmp_key"

pytest wepn-regression.py -vvv -r w --retries 2 --retry-delay 30
