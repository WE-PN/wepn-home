import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from collections import deque

from constants import (MSG_CHANNEL_MAX_FRAME,
                       MSG_CHANNEL_PROTOCOL_VERSION,
                       MSG_CHANNEL_SOCKET)

_HEADER = struct.Struct(">I")
_PEERCRED_STRUCT = struct.Struct("3i")
KEEPALIVE_SECONDS = 60
RECONNECT_MIN_SECONDS = 1
RECONNECT_MAX_SECONDS = 60


class ChannelError(Exception):
    pass


class ChannelClosed(ChannelError):
    pass


class FrameTooLarge(ChannelError):
    pass


def pack_frame(frame):
    data = json.dumps(frame).encode("utf-8")
    if len(data) > MSG_CHANNEL_MAX_FRAME:
        raise FrameTooLarge("frame of %d bytes exceeds limit" % len(data))
    return _HEADER.pack(len(data)) + data


def _recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ChannelClosed("connection closed by peer")
        buf += chunk
    return buf


def read_frame(sock):
    (length,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    if length > MSG_CHANNEL_MAX_FRAME:
        raise FrameTooLarge("frame of %d bytes exceeds limit" % length)
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


def _new_frame(frame_type, **fields):
    frame = {"v": MSG_CHANNEL_PROTOCOL_VERSION,
             "type": frame_type,
             "id": str(uuid.uuid4())}
    frame.update(fields)
    return frame


class _Connection:
    def __init__(self, sock, peer_pid=0):
        self.sock = sock
        self.peer_pid = peer_pid
        self.role = None
        self.write_lock = threading.Lock()

    def send(self, frame):
        with self.write_lock:
            self.sock.sendall(pack_frame(frame))

    def close(self):
        # shutdown first: close() alone does not wake a thread blocked in
        # recv() on this socket, so the peer would never see the FIN
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class ChannelServer:
    """Unix-socket hub the main pproxy process listens on.

    Producers (mqtt forwarder, message poller, future sources) connect,
    identify with a hello frame, then publish 'msg' and 'state' frames.
    The server can push 'cmd' frames back down a producer's connection.
    """

    def __init__(self, on_message, on_state=None,
                 socket_path=MSG_CHANNEL_SOCKET,
                 allowed_uids=None, logger=None):
        self.on_message = on_message
        self.on_state = on_state
        self.socket_path = socket_path
        if allowed_uids is None:
            allowed_uids = {os.getuid(), 0}
        self.allowed_uids = set(allowed_uids)
        self.logger = logger or logging.getLogger("msg_channel")
        self.server_sock = None
        self.clients = {}  # role -> _Connection
        self.clients_lock = threading.Lock()
        self.pending_cmds = {}  # frame id -> {"event": Event, "response": dict}
        self.pending_lock = threading.Lock()
        self.running = False

    def start(self):
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(self.socket_path)
        # owner+group only: producers run as the same user (or root),
        # and every connection is also SO_PEERCRED-checked
        os.chmod(self.socket_path, 0o660)  # nosec B103
        self.server_sock.listen(5)
        self.running = True
        accept_thread = threading.Thread(target=self._accept_loop,
                                         name="channel-accept", daemon=True)
        accept_thread.start()
        self.logger.info("channel server listening on " + self.socket_path)

    def stop(self):
        self.running = False
        if self.server_sock is not None:
            # shutdown also wakes the thread blocked in accept()
            try:
                self.server_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.server_sock.close()
            except OSError:
                pass
        with self.clients_lock:
            conns = list(self.clients.values())
            self.clients.clear()
        for conn in conns:
            conn.close()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def _peer_allowed(self, sock):
        try:
            creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                    _PEERCRED_STRUCT.size)
            pid, uid, gid = _PEERCRED_STRUCT.unpack(creds)
        except OSError:
            self.logger.exception("could not read peer credentials")
            return False, 0
        if uid not in self.allowed_uids:
            self.logger.error("rejecting peer with uid %d (pid %d)" % (uid, pid))
            return False, pid
        return True, pid

    def _accept_loop(self):
        while self.running:
            try:
                sock, _ = self.server_sock.accept()
            except OSError:
                if self.running:
                    self.logger.exception("accept failed")
                return
            allowed, pid = self._peer_allowed(sock)
            if not allowed:
                sock.close()
                continue
            conn = _Connection(sock, peer_pid=pid)
            reader = threading.Thread(target=self._serve_connection, args=(conn,),
                                      name="channel-reader", daemon=True)
            reader.start()

    def _serve_connection(self, conn):
        try:
            if not self._handshake(conn):
                return
            while self.running:
                frame = read_frame(conn.sock)
                self._handle_frame(conn, frame)
        except (ChannelClosed, OSError):
            self.logger.info("connection closed: role=" + str(conn.role))
        except Exception:
            self.logger.exception("error serving connection role=" + str(conn.role))
        finally:
            self._unregister(conn)
            conn.close()

    def _handshake(self, conn):
        frame = read_frame(conn.sock)
        if frame.get("v") != MSG_CHANNEL_PROTOCOL_VERSION:
            conn.send(_new_frame("error", reason="protocol version mismatch"))
            return False
        role = frame.get("role")
        if frame.get("type") != "hello" or not role or not isinstance(role, str):
            conn.send(_new_frame("error", reason="hello expected"))
            return False
        conn.role = role
        with self.clients_lock:
            old = self.clients.get(role)
            self.clients[role] = conn
        if old is not None:
            # a reconnecting producer replaces its dead predecessor
            old.close()
        conn.send(_new_frame("hello-ack", role=role))
        self.logger.info("producer registered: role=" + role)
        return True

    def _unregister(self, conn):
        if conn.role is None:
            return
        with self.clients_lock:
            if self.clients.get(conn.role) is conn:
                del self.clients[conn.role]

    def _handle_frame(self, conn, frame):
        frame_type = frame.get("type")
        frame_id = frame.get("id")
        if frame_type == "msg":
            try:
                result = self.on_message(frame.get("source", conn.role),
                                         frame.get("payload"))
                status = "queued"
                if isinstance(result, dict) and "status" in result:
                    status = result["status"]
                if status == "busy":
                    # overload: not accepted, tell the producer to retry so it
                    # does not treat this as delivered (no ack)
                    conn.send(_new_frame("error", id=frame_id, reason="busy"))
                else:
                    conn.send(_new_frame("ack", id=frame_id, status=status))
            except Exception:
                self.logger.exception("on_message callback failed")
                conn.send(_new_frame("error", id=frame_id, reason="handler failed"))
        elif frame_type == "state":
            if self.on_state is not None:
                try:
                    self.on_state(frame.get("source", conn.role),
                                  int(frame.get("connected", 0)),
                                  frame.get("reason", 0))
                except Exception:
                    self.logger.exception("on_state callback failed")
        elif frame_type == "cmd-ack":
            with self.pending_lock:
                pending = self.pending_cmds.get(frame_id)
            if pending is not None:
                pending["response"] = frame
                pending["event"].set()
        elif frame_type == "ping":
            conn.send(_new_frame("pong", id=frame_id))
        elif frame_type == "pong":
            pass
        else:
            self.logger.warning("ignoring unknown frame type: " + str(frame_type))

    def send_cmd(self, role, cmd, timeout=10):
        """Push a command to a connected producer; returns the cmd-ack frame or None."""
        with self.clients_lock:
            conn = self.clients.get(role)
        if conn is None:
            self.logger.warning("no producer connected for role: " + role)
            return None
        frame = _new_frame("cmd", cmd=cmd)
        pending = {"event": threading.Event(), "response": None}
        with self.pending_lock:
            self.pending_cmds[frame["id"]] = pending
        try:
            conn.send(frame)
            pending["event"].wait(timeout)
            return pending["response"]
        except (ChannelError, OSError):
            self.logger.exception("failed to send cmd to role: " + role)
            return None
        finally:
            with self.pending_lock:
                self.pending_cmds.pop(frame["id"], None)


class ChannelClient:
    """Producer-side client: connects to the hub, publishes frames,
    and receives 'cmd' pushes. Reconnects with backoff; buffers a bounded
    number of unacked 'msg' frames for replay after reconnect."""

    def __init__(self, role, socket_path=MSG_CHANNEL_SOCKET,
                 on_cmd=None, logger=None, max_unacked=100):
        self.role = role
        self.socket_path = socket_path
        self.on_cmd = on_cmd
        self.logger = logger or logging.getLogger("msg_channel")
        self.conn = None
        self.conn_lock = threading.Lock()
        self.connected = threading.Event()
        self.pending_acks = {}  # frame id -> {"event": Event, "response": dict}
        self.pending_lock = threading.Lock()
        self.unacked = deque(maxlen=max_unacked)
        self.unacked_lock = threading.Lock()
        self.last_state = None
        self.running = False

    def start(self):
        self.running = True
        thread = threading.Thread(target=self._run, name="channel-client", daemon=True)
        thread.start()

    def stop(self):
        self.running = False
        self._drop_connection()

    def _drop_connection(self):
        self.connected.clear()
        with self.conn_lock:
            conn = self.conn
            self.conn = None
        if conn is not None:
            conn.close()

    def _run(self):
        backoff = RECONNECT_MIN_SECONDS
        while self.running:
            try:
                self._connect_and_read()
            except (ChannelError, OSError) as err:
                self.logger.debug("channel connection lost: " + str(err))
            except Exception:
                self.logger.exception("unexpected channel client error")
            # a completed session resets the backoff; only consecutive
            # failed connection attempts grow it
            if self.connected.is_set():
                backoff = RECONNECT_MIN_SECONDS
            self._drop_connection()
            if not self.running:
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    def _connect_and_read(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        conn = _Connection(sock)
        conn.send(_new_frame("hello", role=self.role, pid=os.getpid()))
        reply = read_frame(sock)
        if reply.get("type") != "hello-ack":
            raise ChannelError("handshake rejected: " + str(reply))
        with self.conn_lock:
            self.conn = conn
        self.connected.set()
        self.logger.info("connected to channel server as " + self.role)
        self._replay_unacked(conn)
        if self.last_state is not None:
            conn.send(_new_frame("state", source=self.role, **self.last_state))
        sock.settimeout(KEEPALIVE_SECONDS)
        while self.running:
            try:
                frame = read_frame(sock)
            except socket.timeout:
                conn.send(_new_frame("ping"))
                continue
            self._handle_frame(conn, frame)

    def _replay_unacked(self, conn):
        with self.unacked_lock:
            frames = list(self.unacked)
        for frame in frames:
            conn.send(frame)

    def _handle_frame(self, conn, frame):
        frame_type = frame.get("type")
        frame_id = frame.get("id")
        if frame_type == "ack" or frame_type == "error":
            with self.unacked_lock:
                for queued in list(self.unacked):
                    if queued["id"] == frame_id:
                        self.unacked.remove(queued)
                        break
            with self.pending_lock:
                pending = self.pending_acks.get(frame_id)
            if pending is not None:
                pending["response"] = frame
                pending["event"].set()
        elif frame_type == "cmd":
            if self.on_cmd is not None:
                try:
                    self.on_cmd(frame.get("cmd"))
                except Exception:
                    self.logger.exception("on_cmd callback failed")
            conn.send(_new_frame("cmd-ack", id=frame_id))
        elif frame_type == "ping":
            conn.send(_new_frame("pong", id=frame_id))
        elif frame_type == "pong":
            pass
        else:
            self.logger.warning("ignoring unknown frame type: " + str(frame_type))

    def _current_conn(self):
        with self.conn_lock:
            return self.conn

    def send_msg(self, payload, ref=None, timeout=10, replay=True):
        """Publish a message; returns the ack frame, or None if not acked in time.
        With replay=True the frame is kept in a bounded buffer and re-sent after
        a reconnect; use replay=False when the source itself redelivers (e.g. the
        backend keeps unacked messages unread)."""
        frame = _new_frame("msg", source=self.role, payload=payload, ref=ref)
        with self.unacked_lock:
            self.unacked.append(frame)
        pending = {"event": threading.Event(), "response": None}
        with self.pending_lock:
            self.pending_acks[frame["id"]] = pending
        try:
            conn = self._current_conn()
            if conn is not None:
                try:
                    conn.send(frame)
                except (ChannelError, OSError):
                    self.logger.debug("send failed, frame kept for replay")
            pending["event"].wait(timeout)
            return pending["response"]
        finally:
            with self.pending_lock:
                self.pending_acks.pop(frame["id"], None)
            # replay=False sources redeliver on their own (the backend keeps
            # unacked messages unread), so never retain their frames for replay
            # regardless of ack, error, or timeout
            if not replay:
                with self.unacked_lock:
                    try:
                        self.unacked.remove(frame)
                    except ValueError:
                        pass

    def send_state(self, connected, reason=0):
        """Report producer link state (e.g. MQTT broker connectivity) to main.
        The latest state is re-sent automatically after a reconnect."""
        self.last_state = {"connected": int(connected), "reason": reason}
        conn = self._current_conn()
        if conn is None:
            return False
        try:
            conn.send(_new_frame("state", source=self.role, **self.last_state))
            return True
        except (ChannelError, OSError):
            self.logger.debug("state send failed, will resend on reconnect")
            return False
