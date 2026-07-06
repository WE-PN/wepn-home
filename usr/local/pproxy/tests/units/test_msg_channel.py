import os
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

from constants import MSG_CHANNEL_MAX_FRAME
from msg_channel import (ChannelClient, ChannelClosed, ChannelServer,
                         FrameTooLarge, pack_frame, read_frame)


def wait_until(predicate, timeout=5, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestFraming(unittest.TestCase):

    def _pair(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_round_trip(self):
        a, b = self._pair()
        frame = {'v': 1, 'type': 'msg', 'id': 'abc',
                 'payload': {'action': 'reboot_device'}}
        a.sendall(pack_frame(frame))
        self.assertEqual(read_frame(b), frame)

    def test_multiple_frames_in_sequence(self):
        a, b = self._pair()
        frames = [{'v': 1, 'type': 'ping', 'id': str(i)} for i in range(3)]
        for frame in frames:
            a.sendall(pack_frame(frame))
        for frame in frames:
            self.assertEqual(read_frame(b), frame)

    def test_pack_oversize_rejected(self):
        with self.assertRaises(FrameTooLarge):
            pack_frame({'payload': 'x' * (MSG_CHANNEL_MAX_FRAME + 1)})

    def test_read_oversize_rejected(self):
        a, b = self._pair()
        a.sendall(struct.pack('>I', MSG_CHANNEL_MAX_FRAME + 1))
        with self.assertRaises(FrameTooLarge):
            read_frame(b)

    def test_closed_socket_raises_channel_closed(self):
        a, b = self._pair()
        a.close()
        with self.assertRaises(ChannelClosed):
            read_frame(b)

    def test_truncated_frame_raises_channel_closed(self):
        a, b = self._pair()
        data = pack_frame({'v': 1, 'type': 'ping', 'id': 'x'})
        a.sendall(data[:len(data) - 2])
        a.close()
        with self.assertRaises(ChannelClosed):
            read_frame(b)


class TestServerClient(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.socket_path = os.path.join(self.tmpdir, 'test_channel.sock')
        self.received = []
        self.states = []
        self.msg_event = threading.Event()
        self.state_event = threading.Event()
        self.server = self._make_server()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.stop()

    def _make_server(self, **kwargs):
        def on_message(source, payload):
            self.received.append((source, payload))
            self.msg_event.set()
            return {'status': 'queued'}

        def on_state(source, connected, reason):
            self.states.append((source, connected, reason))
            self.state_event.set()

        kwargs.setdefault('socket_path', self.socket_path)
        kwargs.setdefault('logger', MagicMock())
        return ChannelServer(on_message=on_message, on_state=on_state, **kwargs)

    def _make_client(self, role='tester', **kwargs):
        client = ChannelClient(role, socket_path=self.socket_path,
                               logger=MagicMock(), **kwargs)
        client.start()
        self.clients.append(client)
        return client

    def test_hello_registers_role(self):
        client = self._make_client(role='mqtt')
        self.assertTrue(client.connected.wait(5))
        self.assertTrue(wait_until(lambda: 'mqtt' in self.server.clients))

    def test_send_msg_acked_and_delivered(self):
        client = self._make_client(role='mqtt')
        self.assertTrue(client.connected.wait(5))
        ack = client.send_msg({'action': 'reboot_device'}, timeout=5)
        self.assertIsNotNone(ack)
        self.assertEqual(ack.get('type'), 'ack')
        self.assertEqual(ack.get('status'), 'queued')
        self.assertEqual(self.received, [('mqtt', {'action': 'reboot_device'})])

    def test_send_state_reaches_server(self):
        client = self._make_client(role='mqtt')
        self.assertTrue(client.connected.wait(5))
        client.send_state(1, 0)
        self.assertTrue(self.state_event.wait(5))
        self.assertEqual(self.states, [('mqtt', 1, 0)])

    def test_send_cmd_round_trip(self):
        cmds = []
        cmd_event = threading.Event()

        def on_cmd(cmd):
            cmds.append(cmd)
            cmd_event.set()

        client = self._make_client(role='poller', on_cmd=on_cmd)
        self.assertTrue(client.connected.wait(5))
        self.assertTrue(wait_until(lambda: 'poller' in self.server.clients))
        ack = self.server.send_cmd('poller', {'cmd': 'fetch-now'}, timeout=5)
        self.assertTrue(cmd_event.wait(5))
        self.assertEqual(cmds, [{'cmd': 'fetch-now'}])
        self.assertIsNotNone(ack)
        self.assertEqual(ack.get('type'), 'cmd-ack')

    def test_send_cmd_unknown_role_returns_none(self):
        self.assertIsNone(self.server.send_cmd('nobody', {'cmd': 'fetch-now'},
                                               timeout=1))

    def test_unacked_replayed_after_reconnect(self):
        client = self._make_client(role='mqtt')
        self.assertTrue(client.connected.wait(5))
        self.server.stop()
        self.assertTrue(wait_until(lambda: not client.connected.is_set()))
        # not acked: no server; frame must stay buffered
        ack = client.send_msg({'action': 'start_service'}, timeout=0.2)
        self.assertIsNone(ack)
        self.server = self._make_server()
        self.server.start()
        self.assertTrue(self.msg_event.wait(10))
        self.assertEqual(self.received, [('mqtt', {'action': 'start_service'})])

    def test_replay_false_drops_unacked_frame(self):
        client = self._make_client(role='poller')
        self.assertTrue(client.connected.wait(5))
        self.server.stop()
        self.assertTrue(wait_until(lambda: not client.connected.is_set()))
        ack = client.send_msg({'action': 'start_service'}, timeout=0.2,
                              replay=False)
        self.assertIsNone(ack)
        self.assertEqual(len(client.unacked), 0)

    def test_disallowed_uid_rejected(self):
        self.server.stop()
        # pretend only some other uid is allowed
        self.server = self._make_server(allowed_uids={os.getuid() + 54321})
        self.server.start()
        client = self._make_client(role='mqtt')
        self.assertFalse(client.connected.wait(1.5))
        self.assertEqual(self.server.clients, {})

    def test_invalid_hello_rejected(self):
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(raw.close)
        raw.connect(self.socket_path)
        raw.sendall(pack_frame({'v': 1, 'type': 'msg', 'id': 'x',
                                'payload': {}}))
        reply = read_frame(raw)
        self.assertEqual(reply.get('type'), 'error')

    def test_protocol_version_mismatch_rejected(self):
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(raw.close)
        raw.connect(self.socket_path)
        raw.sendall(pack_frame({'v': 999, 'type': 'hello', 'id': 'x',
                                'role': 'mqtt'}))
        reply = read_frame(raw)
        self.assertEqual(reply.get('type'), 'error')

    def test_socket_permissions(self):
        mode = os.stat(self.socket_path).st_mode
        self.assertEqual(mode & 0o777, 0o660)

    def test_connection_cap_rejects_excess(self):
        self.server.stop()
        self.server = self._make_server(max_connections=1)
        self.server.start()
        client = self._make_client(role='a')
        self.assertTrue(client.connected.wait(5))
        self.assertTrue(wait_until(lambda: 'a' in self.server.clients))
        # a second connection has no slot; server accepts then closes it
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(raw.close)
        raw.connect(self.socket_path)
        raw.settimeout(3)
        self.assertEqual(raw.recv(16), b'')

    def test_handshake_timeout_closes_silent_peer(self):
        self.server.stop()
        self.server = self._make_server(handshake_timeout=0.5)
        self.server.start()
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(raw.close)
        raw.connect(self.socket_path)
        raw.settimeout(3)
        # never send hello; the server must close us after handshake_timeout
        self.assertEqual(raw.recv(16), b'')

    def test_idle_read_timeout_closes_stalled_peer(self):
        self.server.stop()
        self.server = self._make_server(handshake_timeout=2, idle_timeout=0.5)
        self.server.start()
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(raw.close)
        raw.connect(self.socket_path)
        raw.settimeout(3)
        raw.sendall(pack_frame({'v': 1, 'type': 'hello', 'id': 'x',
                                'role': 'stall'}))
        self.assertEqual(read_frame(raw).get('type'), 'hello-ack')
        # then go silent past idle_timeout; server closes us
        self.assertEqual(raw.recv(16), b'')


if __name__ == '__main__':
    unittest.main()
