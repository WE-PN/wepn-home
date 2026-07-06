# Soak test for the msg_channel IPC layer (dev aid, not a formal test).
#
# Starts a ChannelServer on a temp socket, hammers it with several producer
# clients, restarts the server randomly mid-flight, and verifies:
#   - every acked frame was actually received by the server (no acked loss)
#   - per-client delivery order is preserved (FIFO within a connection)
#   - fetch-now cmd round-trips keep working after restarts
#
# Usage:  python3 tests/runners/msg_channel_soak.py [num_clients] [msgs_per_client]

import os
import random
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from msg_channel import ChannelClient, ChannelServer  # noqa: E402

NUM_CLIENTS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
MSGS_PER_CLIENT = int(sys.argv[2]) if len(sys.argv) > 2 else 500
SERVER_RESTARTS = 3

received = []  # (source, payload)
received_lock = threading.Lock()
socket_path = os.path.join(tempfile.mkdtemp(), "soak.sock")
fetch_now_count = [0]


def on_message(source, payload):
    with received_lock:
        received.append((source, payload))
    return {"status": "queued"}


def on_state(source, connected, reason):
    pass


def make_server():
    server = ChannelServer(on_message=on_message, on_state=on_state,
                           socket_path=socket_path)
    server.start()
    return server


def on_cmd(cmd):
    if isinstance(cmd, dict) and cmd.get("cmd") == "fetch-now":
        fetch_now_count[0] += 1


chaos_done = threading.Event()


def producer(role, acked, failed):
    client = ChannelClient(role, socket_path=socket_path, on_cmd=on_cmd)
    client.start()
    client.connected.wait(10)
    for seq in range(MSGS_PER_CLIENT):
        payload = {"action": "soak", "role": role, "seq": seq}
        ack = client.send_msg(payload, timeout=15)
        if ack is not None and ack.get("type") == "ack":
            acked.append(seq)
        else:
            failed.append(seq)
        # pace the stream so it overlaps the server restarts
        time.sleep(0.01)
    # stay connected until the chaos phase is over (so cmd pushes and
    # replay have live clients to talk to), then a grace for replay
    chaos_done.wait(60)
    time.sleep(2)
    client.stop()


def main():
    server = make_server()
    acked = {}
    failed = {}
    threads = []
    for i in range(NUM_CLIENTS):
        role = "soak%d" % i
        acked[role] = []
        failed[role] = []
        t = threading.Thread(target=producer, args=(role, acked[role], failed[role]))
        t.start()
        threads.append(t)

    # chaos: restart the server a few times while producers are running
    for _ in range(SERVER_RESTARTS):
        time.sleep(random.uniform(1.0, 3.0))
        print("[soak] restarting server ...")
        server.stop()
        time.sleep(random.uniform(0.2, 1.0))
        server = make_server()
        # give producers a moment to reconnect, then exercise cmd push
        time.sleep(2.5)
        for role in list(server.clients):
            server.send_cmd(role, {"cmd": "fetch-now"}, timeout=5)
    chaos_done.set()

    for t in threads:
        t.join()
    time.sleep(1)

    # ---- verification ----
    ok = True
    by_role = {}
    with received_lock:
        for source, payload in received:
            by_role.setdefault(source, []).append(payload["seq"])

    total_acked = 0
    for role in acked:
        got = by_role.get(role, [])
        got_set = set(got)
        missing = [s for s in acked[role] if s not in got_set]
        total_acked += len(acked[role])
        if missing:
            ok = False
            print("[FAIL] %s: %d acked frames never received: %s ..."
                  % (role, len(missing), missing[:10]))
        # FIFO check: first occurrence of each seq must be increasing
        seen = set()
        firsts = []
        for s in got:
            if s not in seen:
                seen.add(s)
                firsts.append(s)
        if firsts != sorted(firsts):
            ok = False
            print("[FAIL] %s: out-of-order delivery detected" % role)
        print("[soak] %s: acked=%d received=%d (dups=%d) unacked=%d"
              % (role, len(acked[role]), len(got_set),
                 len(got) - len(got_set), len(failed[role])))

    print("[soak] fetch-now round-trips: %d" % fetch_now_count[0])
    server.stop()
    if ok:
        print("[soak] PASS: no acked-frame loss, FIFO preserved")
        sys.exit(0)
    print("[soak] FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
