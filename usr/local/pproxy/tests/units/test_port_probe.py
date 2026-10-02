import os
import socket
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

#autopep8: off
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)
import constants
import port_probe
from port_probe import HTTP_OK_RESPONSE, PortProbe, parse_test_port, run_listener
#autopep8: on


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestParseTestPort:
    def test_valid_bounds(self):
        assert parse_test_port(constants.TEST_PORT_MIN) == constants.TEST_PORT_MIN
        assert parse_test_port(constants.TEST_PORT_MAX) == constants.TEST_PORT_MAX
        assert parse_test_port(5100) == 5100

    def test_valid_numeric_string(self):
        assert parse_test_port("5100") == 5100

    def test_out_of_range(self):
        assert parse_test_port(constants.TEST_PORT_MIN - 1) is None
        assert parse_test_port(constants.TEST_PORT_MAX + 1) is None
        assert parse_test_port(0) is None
        assert parse_test_port(-5100) is None
        assert parse_test_port(65536) is None

    def test_wrong_types(self):
        assert parse_test_port(None) is None
        assert parse_test_port(True) is None
        assert parse_test_port(False) is None
        assert parse_test_port(5100.0) is None
        assert parse_test_port([5100]) is None
        assert parse_test_port("51.0") is None
        assert parse_test_port("5100 ") is None
        assert parse_test_port("-5100") is None
        assert parse_test_port("") is None


class TestRunListener:
    def probe(self, port, payload=b"GET / HTTP/1.0\r\n\r\n"):
        conn = socket.create_connection(('127.0.0.1', port), timeout=5)
        conn.sendall(payload)
        conn.settimeout(5)
        chunks = []
        while True:
            data = conn.recv(4096)
            if not data:
                break
            chunks.append(data)
        conn.close()
        return b"".join(chunks)

    def start_listener(self, port, stop_event, response_mode):
        bound_event = threading.Event()
        t = threading.Thread(
            target=run_listener,
            args=(MagicMock(), '127.0.0.1', port, stop_event, 30),
            kwargs={"response_mode": response_mode, "bound_event": bound_event,
                    "accept_timeout": 0.2},
            daemon=True)
        t.start()
        assert bound_event.wait(5)
        return t

    def test_http_ok_fixed_response(self):
        port = get_free_port()
        stop_event = threading.Event()
        t = self.start_listener(port, stop_event, "http-ok")
        try:
            marker = b"SHOULD-NOT-BE-REFLECTED"
            response = self.probe(port, b"GET /" + marker + b" HTTP/1.0\r\n\r\n")
            assert response == HTTP_OK_RESPONSE
            assert marker not in response
            # listener stays up and serves a second probe
            assert self.probe(port) == HTTP_OK_RESPONSE
        finally:
            stop_event.set()
            t.join(timeout=5)
        assert not t.is_alive()

    def test_echo_mode(self):
        port = get_free_port()
        stop_event = threading.Event()
        t = self.start_listener(port, stop_event, "echo")
        try:
            assert self.probe(port, b"12345678") == b"12345678"
        finally:
            stop_event.set()
            t.join(timeout=5)

    def test_bind_failure_no_bound_event(self):
        port = get_free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(('127.0.0.1', port))
        blocker.listen(1)
        try:
            logger = MagicMock()
            bound_event = threading.Event()
            run_listener(logger, '127.0.0.1', port, threading.Event(), 30,
                         response_mode="http-ok", bound_event=bound_event)
            assert not bound_event.is_set()
            logger.error.assert_called_once()
        finally:
            blocker.close()

    def test_deadline_expires(self):
        port = get_free_port()
        stop_event = threading.Event()
        bound_event = threading.Event()
        t = threading.Thread(
            target=run_listener,
            args=(MagicMock(), '127.0.0.1', port, stop_event, 0.5),
            kwargs={"response_mode": "http-ok", "bound_event": bound_event,
                    "accept_timeout": 0.1},
            daemon=True)
        t.start()
        assert bound_event.wait(5)
        t.join(timeout=5)
        assert not t.is_alive()


class TestPortProbe:
    @pytest.fixture
    def probe(self, monkeypatch):
        monkeypatch.setattr(port_probe, 'TEST_PORT_WINDOW_SECONDS', 1)
        monkeypatch.setattr(port_probe, 'TEST_PORT_COOLDOWN_SECONDS', 1)
        return PortProbe(MagicMock(), MagicMock(), MagicMock())

    def wait_for_idle(self, probe, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with probe._lock:
                if probe._active_port is None:
                    return
            time.sleep(0.05)
        raise AssertionError("probe session did not finish")

    def test_invalid_port_rejected(self, probe):
        assert probe.request("bogus") is False
        probe.device.open_port.assert_not_called()

    def test_full_session(self, probe, monkeypatch):
        # a random free localhost port is outside 5001-5500, so bypass the
        # validator (covered by TestParseTestPort) to avoid flaky binds
        port = get_free_port()
        monkeypatch.setattr(port_probe, 'parse_test_port', lambda v: port)
        assert probe.request(port) is True
        self.wait_for_idle(probe)
        probe.device.open_port.assert_called_once_with(
            port=port, text='adhoc probe port',
            timeout=1 + constants.TEST_PORT_LEASE_MARGIN_SECONDS,
            protos=("TCP",))
        probe.device.close_port.assert_called_once_with(port, protos=("TCP",))
        probe.messages.send_msg.assert_called_once()
        kwargs = probe.messages.send_msg.call_args.kwargs
        assert kwargs["destination"] == "BACKEND"
        assert kwargs["secure"] is False
        assert kwargs["msg_type"] == "response-test-port"
        assert kwargs["extra_fields"]["port"] == port
        assert kwargs["extra_fields"]["window_seconds"] == 1
        assert "expires_at" not in kwargs["extra_fields"]
        assert kwargs["expires_at"] > time.time() - 5

    def test_overlap_rejected(self, probe, monkeypatch):
        monkeypatch.setattr(port_probe, 'TEST_PORT_WINDOW_SECONDS', 5)
        port = get_free_port()
        monkeypatch.setattr(port_probe, 'parse_test_port', lambda v: port)
        assert probe.request(port) is True
        # second request while the first is mid-window
        time.sleep(0.2)
        assert probe.request(port) is False

    def test_cooldown_enforced(self, probe, monkeypatch):
        port = get_free_port()
        monkeypatch.setattr(port_probe, 'parse_test_port', lambda v: port)
        assert probe.request(port) is True
        self.wait_for_idle(probe)
        assert probe.request(port) is False

    def test_bind_failure_skips_upnp_and_ack(self, probe, monkeypatch):
        port = get_free_port()
        monkeypatch.setattr(port_probe, 'parse_test_port', lambda v: port)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(('', port))
        blocker.listen(1)
        try:
            assert probe.request(port) is True
            self.wait_for_idle(probe)
            probe.device.open_port.assert_not_called()
            probe.messages.send_msg.assert_not_called()
        finally:
            blocker.close()
