# Informal on-Pod helper: decrypt an E2EE message addressed to this device.
#
# Uses the same AES-256-GCM path as messages.Messages.decrypt_message, with the
# key taken from status.ini [status] e2e_key (set during QR onboarding).
#
# Usage:
#   # ciphertext + nonce straight off the wire (both urlsafe-b64):
#   wepn-env python tests/runners/decrypt_message.py <ciphertext_b64> <nonce_b64>
#
#   # or hand it a whole message_body JSON blob (needs "message" + "nonce"):
#   wepn-env python tests/runners/decrypt_message.py --json '{"message": "...", "nonce": "..."}'
#   cat body.json | wepn-env python tests/runners/decrypt_message.py --json -
#
#   # sanity check: encrypt a string, then decrypt it back
#   wepn-env python tests/runners/decrypt_message.py --encrypt "hello there"

import argparse
import json
import string
import sys
import os

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)

from messages import Messages  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ciphertext", nargs="?",
                        help="urlsafe-b64 ciphertext (with GCM tag appended)")
    parser.add_argument("nonce", nargs="?",
                        help="urlsafe-b64 nonce")
    parser.add_argument("--json", dest="body",
                        help="message_body JSON (or '-' to read it from stdin)")
    parser.add_argument("--encrypt", metavar="TEXT",
                        help="encrypt TEXT with this device's key, print ciphertext/nonce, "
                             "then decrypt it back as a round-trip check")
    args = parser.parse_args()

    messages = Messages()
    if not messages.e2ee_available():
        print("no e2e_key in status.ini -- this device has no E2EE key set up", file=sys.stderr)
        return 2

    if args.encrypt is not None:
        import base64
        secure_text, nonce = messages.encrypt_message(args.encrypt)
        ct_b64 = base64.urlsafe_b64encode(secure_text).decode("utf-8")
        nonce_b64 = base64.urlsafe_b64encode(nonce).decode("utf-8")
        print("ciphertext:", ct_b64)
        print("nonce:     ", nonce_b64)
        print("decrypted: ", messages.decrypt_message(ct_b64, nonce_b64))
        return 0

    if args.body is not None:
        raw = sys.stdin.read() if args.body == "-" else args.body
        body = json.loads(raw)
        ciphertext, nonce = body["message"], body["nonce"]
    else:
        ciphertext, nonce = args.ciphertext, args.nonce

    if not ciphertext or not nonce:
        parser.error("need <ciphertext> <nonce>, or --json, or --encrypt")

    dec_msg = messages.decrypt_message(ciphertext, nonce)
    print("all characters decrypted are pritable?", all(c in string.printable for c in dec_msg))
    print(dec_msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
