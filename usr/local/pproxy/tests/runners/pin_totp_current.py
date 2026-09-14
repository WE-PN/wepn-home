# Informal on-Pod helper: print the currently expected local_token (the
# TOTP-derived value for the current 900s time step), computed straight from
# status.ini's pin -- independent of whatever heartbeat.py last wrote to
# status.ini's local_token field.
#
# See docs/pin-totp-local-token-plan.md / usr/local/pproxy/pin_totp.py for
# the derivation this reproduces.
#
# Usage:
#   wepn-env python tests/runners/pin_totp_current.py
#   wepn-env python tests/runners/pin_totp_current.py --pin ABCDEFGHJKLMNPQR

import argparse
import logging
import os
import sys

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)

import pin_totp  # noqa: E402
from wstatus import WStatus  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", help="derive for this pin instead of reading status.ini")
    args = parser.parse_args()

    if args.pin:
        pin = args.pin
    else:
        pin = WStatus(logging.getLogger("pin_totp_current")).get('pin')

    if not pin_totp.is_valid_pin_format(pin):
        print("not a valid-format pin: %r" % pin, file=sys.stderr)
        return 2

    print(pin_totp.derive_local_token(pin))
    return 0


if __name__ == "__main__":
    sys.exit(main())
