# Informal on-Pod validation for the ad-hoc test port feature (port_probe.py).
#
# Runs a full PortProbe session against the real Device (UPnP), probes the
# listener locally, checks the overlap guard, then waits out the window and
# verifies teardown. The ack is stubbed by default; pass --send-ack to use
# the real Messages path (sends a real message to the backend).
#
# Usage:  wepn-env python tests/runners/port_probe_runner.py [--port 5100] [--send-ack]

import argparse
import logging
import os
import socket
import sys
import time
from unittest.mock import MagicMock

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)

from constants import TEST_PORT_WINDOW_SECONDS  # noqa: E402
from device import Device  # noqa: E402
from port_probe import PortProbe  # noqa: E402

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("port_probe_runner")


def probe_once(port):
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn.sendall(b"GET / HTTP/1.0\r\n\r\n")
    conn.settimeout(5)
    chunks = []
    while True:
        data = conn.recv(4096)
        if not data:
            break
        chunks.append(data)
    conn.close()
    return b"".join(chunks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--send-ack", action="store_true",
                        help="send the real ack message to the backend")
    args = parser.parse_args()

    device = Device(logger)
    if args.send_ack:
        from messages import Messages
        messages = Messages(logger)
    else:
        messages = MagicMock()

    probe = PortProbe(logger, device, messages)

    print(f"requesting test port {args.port} ...")  # noqa: T201
    if not probe.request(args.port):
        print("FAIL: request was rejected")  # noqa: T201
        return 1
    time.sleep(5)  # give bind + UPnP a moment

    resp = probe_once(args.port)
    print(f"probe 1 response: {resp!r}")  # noqa: T201
    resp2 = probe_once(args.port)
    print(f"probe 2 response: {resp2!r}")  # noqa: T201

    print("sending overlapping request (should be rejected) ...")  # noqa: T201
    overlapped = probe.request(args.port)
    print(f"overlap accepted={overlapped} (expect False)")  # noqa: T201

    print(f"waiting out the {TEST_PORT_WINDOW_SECONDS}s window ...")  # noqa: T201
    time.sleep(TEST_PORT_WINDOW_SECONDS + 10)

    if not args.send_ack:
        print(f"stubbed ack calls: {messages.send_msg.call_args_list}")  # noqa: T201

    try:
        mapping = device.get_port_mapping_by_port(args.port)
        print(f"mapping after window (expect none/error): {mapping}")  # noqa: T201
    except Exception as err:
        print(f"mapping lookup raised (expected once removed): {err}")  # noqa: T201

    try:
        probe_once(args.port)
        print("FAIL: listener still answering after window")  # noqa: T201
        return 1
    except OSError:
        print("listener is down after window, as expected")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
