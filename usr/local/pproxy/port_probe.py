# Time-boxed TCP listeners used for port reachability tests.
#
# run_listener() is the shared accept loop used both by the periodic
# self-test (diag.py, echo mode) and by the ad-hoc "open-test-port"
# action (http-ok mode). PortProbe owns the lifecycle of an ad-hoc test:
# bind -> UPnP forward -> ack -> wait out the window -> teardown.

import socket
import threading
import time

from constants import (
    TEST_PORT_MIN,
    TEST_PORT_MAX,
    TEST_PORT_WINDOW_SECONDS,
    TEST_PORT_LEASE_MARGIN_SECONDS,
    TEST_PORT_ACCEPT_TIMEOUT_SECONDS,
    TEST_PORT_CONN_TIMEOUT_SECONDS,
    TEST_PORT_MAX_RECV_BYTES,
    TEST_PORT_COOLDOWN_SECONDS,
)

# fixed response, never derived from request bytes
HTTP_OK_RESPONSE = (b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: 2\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"OK")


def parse_test_port(value):
    # bool is an int subclass; a JSON payload can carry either an int
    # or a numeric string depending on the sender
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    if value < TEST_PORT_MIN or value > TEST_PORT_MAX:
        return None
    return value


def run_listener(logger, host, port, stop_event, deadline_seconds,
                 response_mode="echo", bound_event=None,
                 accept_timeout=TEST_PORT_ACCEPT_TIMEOUT_SECONDS):
    logger.debug("listener starting..." + str(port))
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(accept_timeout)
    try:
        s.bind((host, int(port)))
    except OSError as err:
        logger.error("OSError in opening listener: " + str(err))
        s.close()
        return
    try:
        s.listen(1)
        if bound_event is not None:
            bound_event.set()
        logger.info(f'listening on port {port}')
        while not stop_event.is_set() and time.time() - start < deadline_seconds:
            try:
                # short timeout so the stop_event and deadline are
                # re-checked; an idle tick is normal and not logged
                conn, addr = s.accept()
            except (TimeoutError, socket.timeout):
                continue
            logger.info(f"Connected by {addr[0]} to port {port}")
            try:
                conn.settimeout(TEST_PORT_CONN_TIMEOUT_SECONDS)
                if response_mode == "http-ok":
                    # drain a bounded amount and discard; the reply is
                    # a constant so request bytes are never reflected
                    conn.recv(TEST_PORT_MAX_RECV_BYTES)
                    conn.sendall(HTTP_OK_RESPONSE)
                else:
                    data = conn.recv(8)
                    conn.sendall(data)
            except OSError as err:
                logger.debug(f"connection error on port {port}: {err}")
            finally:
                conn.close()
    finally:
        s.close()
        logger.debug("listener stopped for port " + str(port))


class PortProbe:
    # one ad-hoc test port session at a time, requested via the
    # "open-test-port" message action
    def __init__(self, logger, device, messages):
        self.logger = logger
        self.device = device
        self.messages = messages
        self._lock = threading.Lock()
        self._active_port = None
        self._cooldown_until = 0.0

    def request(self, port_raw):
        # runs on the message dispatch thread; must return quickly
        port = parse_test_port(port_raw)
        if port is None:
            self.logger.warning("rejected invalid test port request: " + repr(port_raw))
            return False
        with self._lock:
            if self._active_port is not None:
                self.logger.warning("rejected test port %s: port %s already active"
                                    % (port, self._active_port))
                return False
            if time.monotonic() < self._cooldown_until:
                self.logger.warning("rejected test port %s: in cooldown" % port)
                return False
            self._active_port = port
        worker = threading.Thread(target=self._run, args=(port,), daemon=True)
        worker.start()
        return True

    def _run(self, port):
        stop_event = threading.Event()
        bound_event = threading.Event()
        try:
            listener = threading.Thread(
                target=run_listener,
                args=(self.logger, '', port, stop_event, TEST_PORT_WINDOW_SECONDS),
                kwargs={"response_mode": "http-ok", "bound_event": bound_event},
                daemon=True)
            listener.start()
            if not bound_event.wait(2):
                # bind failed (port in use locally): nothing was exposed,
                # so skip the UPnP forward and the ack entirely
                self.logger.error("test port %s could not bind, aborting" % port)
                return
            # lease outlives the window so the router cleans up the
            # mapping on its own if we crash before close_port
            self.device.open_port(
                port=port, text='adhoc probe port',
                timeout=TEST_PORT_WINDOW_SECONDS + TEST_PORT_LEASE_MARGIN_SECONDS,
                protos=("TCP",))
            # ack means "listener is up", not "port is reachable" -- the
            # server's own probe is the ground truth, so send it even if
            # the UPnP circuit breaker skipped the forward
            self._send_ack(port)
            stop_event.wait(TEST_PORT_WINDOW_SECONDS)
        finally:
            stop_event.set()
            try:
                self.device.close_port(port, protos=("TCP",))
            except Exception as err:
                self.logger.error("failed to close test port %s: %s" % (port, err))
            with self._lock:
                self._active_port = None
                self._cooldown_until = time.monotonic() + TEST_PORT_COOLDOWN_SECONDS

    def _send_ack(self, port):
        try:
            # not E2EE: the e2e key is device<->app, and this reply is
            # consumed server-side; payload contains nothing secret
            self.messages.send_msg(
                "", destination="BACKEND", secure=False,
                msg_type="response-test-port",
                extra_fields={"port": port,
                              "window_seconds": TEST_PORT_WINDOW_SECONDS},
                expires_at=int(time.time()) + TEST_PORT_WINDOW_SECONDS)
        except Exception as err:
            self.logger.error("failed to send test port ack: " + str(err))
