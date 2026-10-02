# Informal on-Pod helper: verify the TOTP-derived local_token against
# status.ini's pin and (optionally) the local API.
#
# See docs/pin-totp-local-token-plan.md for the design this validates.
#
# Usage:
#   # print pin, current local_token, and grace-window neighbours
#   wepn-env python tests/runners/pin_totp_check.py
#
#   # also hit the local API with the derived token to confirm it's accepted
#   wepn-env python tests/runners/pin_totp_check.py --check-api
#
#   # race regression: run this alongside a manual `send_heartbeat.py` +
#   # forced DHCP renew, and confirm the printed local_token never diverges
#   # between concurrent runs at the same moment

import argparse
import logging
import os
import ssl
import sys
import urllib.request

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)

import pin_totp  # noqa: E402
from wstatus import WStatus  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-api", action="store_true",
                        help="also call the local API with the derived token")
    args = parser.parse_args()

    logger = logging.getLogger("pin_totp_check")
    status = WStatus(logger)
    pin = status.get('pin')

    if not pin_totp.is_valid_pin_format(pin):
        print("status.ini has no valid-format pin yet (value=%r) -- "
              "trigger a heartbeat first (send_heartbeat.py)" % pin, file=sys.stderr)
        return 2

    step = pin_totp.current_step()
    print("pin:          ", pin)
    print("current step: ", step)
    for offset in (-1, 0, 1):
        code = pin_totp.derive_code(pin, step + offset, pin_totp.PIN_TOTP_PURPOSE_LOCAL_TOKEN)
        label = {-1: "previous", 0: "current", 1: "next"}[offset]
        print("  %-8s local_token: %s" % (label, code))

    current_token = pin_totp.derive_local_token(pin)
    print("status.ini local_token:", status.get('local_token'))
    print("independently derived: ", current_token)
    if str(status.get('local_token')) != str(current_token):
        print("MISMATCH: status.ini has not been refreshed with the current "
              "step's value yet (stale until the next heartbeat runs)")

    if args.check_api:
        url = ("https://127.0.0.1:5000/api/v1/claim/progress?local_token=%s"
              % current_token)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(url, timeout=5, context=ctx) as resp:  # nosec B310
                print("local API status:", resp.status)
                print(resp.read().decode())
        except Exception as err:
            print("local API call failed:", err, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
