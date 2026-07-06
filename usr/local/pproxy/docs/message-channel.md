# Message Channel: Multi-Process Message Handling

## Why

Previously, the main daemon (`pproxy.py`) blocked inside the MQTT client loop
(`loop_forever()`). Every command arrived either as a raw MQTT payload
(dispatched onto an ad-hoc thread per message) or via the backend messaging
API (`/api/message/`), which was fetched **only** when an MQTT `notification`
message arrived. Consequences:

- If MQTT was down, messaging-API commands were never fetched.
- The main process was captive to the MQTT connection lifecycle; a broker
  problem could stall everything else.
- Thread-per-message meant no ordering guarantees and shared-state races.

Message *ingestion* is now split into dedicated producer processes that
publish to the main process over a local IPC channel. The main process owns a
single ordered dispatch queue.

## Architecture

```
        cloud MQTT broker              backend /api/message/
              |                                |
     (paho, TLS, loop_forever)       (GET/PATCH every 5min+jitter,
              |                       AES-GCM decrypt via Messages)
      +---------------+              +------------------+
      | wepn-mqtt     |              | wepn-messages    |
      | mqtt_forwarder|              | message_poller   |
      +-------+-------+              +---------+--------+
              | hello{role:mqtt}               | hello{role:poller}
              | msg / state            msg ->  |  <- cmd{fetch-now}
              v                                v
   ==== /var/local/pproxy/msg_channel.sock (0660, SO_PEERCRED) ====
              |                                |
      +-------+--------------------------------+-------+
      | wepn-main (pproxy.py)                          |
      |  ChannelServer accept loop (thread)            |
      |  per-connection reader threads -> queue.Queue  |
      |  single dispatcher thread -> action handlers   |
      |  (LCD, LEDs, Services, Device, heartbeat)      |
      +------------------------------------------------+
```

| Component | Process / unit | Source |
|---|---|---|
| Channel library (framing, server, client) | shared | `msg_channel.py` |
| MQTT forwarder | `wepn-mqtt.service` (user `pproxy`) | `system_services/mqtt_forwarder.py` |
| Messaging-API poller | `wepn-messages.service` (user `pproxy`) | `system_services/message_poller.py` |
| Hub / dispatcher | `wepn-main.service` (user `pproxy`) | `pproxy.py` |

Key properties:

- **Main owns the socket** (server). Producers are clients that reconnect
  with exponential backoff (1 s → 60 s), so systemd start order is irrelevant.
- **Two FIFO lanes, one consumer each**: the dispatcher thread
  (`PProxy.dispatch_loop`) consumes the intake queue in arrival order and
  handles fast actions inline; actions in `SLOW_ACTIONS` (`add_user`,
  `delete_user` — per-friend work that can take minutes during e.g. an IP
  change burst) are handed to a single slow worker
  (`PProxy.slow_dispatch_loop`). At most two handlers are ever in flight.
  Each lane is strictly ordered, which preserves the guarantee that matters
  (add/delete for the same friend never reorder), while urgent commands like
  `reboot_device` are not stuck behind a burst. The trade-off: a fast action
  can overtake an earlier slow one (e.g. `get-access-link` right after
  `add_user` may run before the add finishes — a race the old threaded code
  had as well). Slow-but-unsafe-to-overlap actions (`update-pproxy`,
  `update-all`, `install-package`) deliberately stay in the fast lane so
  nothing runs while code is being replaced.
- **`notification` handling**: the MQTT `notification` payload carries no
  command body. Main intercepts it before queueing and pushes
  `cmd:{fetch-now}` down the poller's connection, so messaging-API commands
  are picked up immediately instead of waiting for the next poll.
- **At-least-once delivery from the backend**: the poller PATCHes a message
  as read only after main acknowledges the frame. Anything unacked stays
  unread on the backend and is redelivered by the next poll.

## Frame protocol (v1)

Transport: `AF_UNIX` `SOCK_STREAM`, persistent connections. Framing: 4-byte
big-endian unsigned length followed by a UTF-8 JSON object. Maximum frame
size 1 MiB. Unknown frame types and unknown fields are ignored (forward
compatibility); a protocol-version mismatch in `hello` is rejected.

Every frame carries `{"v": 1, "type": ..., "id": "<uuid4>"}`.

| Type | Direction | Fields | Reply |
|---|---|---|---|
| `hello` | producer → main | `role`, `pid` | `hello-ack` (or `error` + close) |
| `msg` | producer → main | `source`, `payload`, `ref` | `ack {id, status}` or `error {id, reason}` |
| `state` | producer → main | `source`, `connected: 0\|1`, `reason` | none |
| `cmd` | main → producer | `cmd` (e.g. `{"cmd": "fetch-now"}`) | `cmd-ack {id}` |
| `ping` / `pong` | both | — | `pong` |

Ack semantics: `status: "queued"` means the payload was accepted into main's
dispatch queue (ack-on-enqueue — producers are never blocked by slow
handlers). `status: "discarded"` means main rejected the payload as invalid
(no `action` field); producers treat both as delivered — retrying a payload
main has rejected cannot succeed.

A producer reconnecting with a role that is already registered replaces the
previous (dead) connection for that role.

## Security model

- The socket is created by `wepn-main` (user `pproxy`) at
  `/var/local/pproxy/msg_channel.sock` and `chmod 0660` before `listen()`.
  A stale socket file is removed at boot by `permissions.sh` and at startup
  by the server itself.
- Every accepted connection is checked with `SO_PEERCRED`: only peers whose
  kernel-verified uid is `pproxy` (or root) are served. This is
  defense-in-depth on top of the filesystem permissions and is strictly
  stronger than a shared token readable from disk by the same uid.
- Message payloads are still untrusted network input. All sanitization
  (`sanitize_str`, regex filtering) stays in the action handlers in
  `pproxy.py`, exactly as before.

## Failure modes

| Failure | Behavior |
|---|---|
| wepn-main down | Producers retry-connect with backoff. The MQTT forwarder buffers up to 100 unacked payloads and replays them on reconnect (also resends its latest `state`). The poller does not buffer: unacked messages stay unread on the backend and are re-fetched next cycle. |
| Producer down | systemd `Restart=on-failure`. For MQTT, `clean_session=False` + QoS 1 means the broker queues messages while the forwarder is down (same as the old in-process behavior). |
| Broker down | Forwarder writes `mqtt=0` to `status.ini` and sends a `state` frame; main pulses the LED ring yellow and heartbeats report the outage. Messaging-API commands still flow via the 5-minute poll — this is the availability win over the old design. |
| Main crashes after ack, before handling | That message is lost (it was marked read). Same window existed in the old code, which marked messages read immediately after spawning the handler thread. A future iteration can add ack-on-handled for the poller role. |
| Duplicate delivery | Possible by design (at-least-once). The poller keeps a bounded set of recently acked backend message ids to avoid re-forwarding within overlapping cycles; across restarts, the backend's read flag is the source of truth. |

## MQTT connection state ownership

`status.ini` fields `mqtt` / `mqtt-reason` are written by the **forwarder**
(reload-before-set-then-save, atomic rename — the existing multi-writer
convention). Rationale: `diag.py` and the cron heartbeat read these fields
directly from `status.ini`; if main owned the writes they would freeze stale
whenever `wepn-main` is down. Main still receives `state` frames to drive
LED feedback, its in-memory `mqtt_connected`/`mqtt_reason` attributes, and
the connect-time heartbeat (`save_state("2")`).

## Unclaimed devices

While `status.ini claimed != 1`, both producers stay idle (no broker
connection, no HTTP polling) and re-check every 30 s. This keeps them out of
the way of `setup/onboard.py`, which uses MQTT as its claim-detection oracle
with candidate keys. When the device is unclaimed, `run.py` runs onboarding
instead of `PProxy`, so the channel socket does not exist either — the whole
pipeline sits dormant until the claim flips.

## Adding a future producer

1. Create a daemon (see `system_services/message_poller.py` as the template):
   instantiate `ChannelClient("<new-role>", on_cmd=...)`, call `start()`,
   then `send_msg(payload)` for each command payload. Payloads must be dicts
   with an `action` key that `PProxy.on_message_handler` understands.
2. Add a systemd unit cloned from `wepn-messages.service`.
3. Enable/start it in `setup/post-install.sh` and `setup/set-services.sh`;
   add it to `/usr/local/sbin/restart-pproxy.sh`.
4. Add a logger section to `etc/pproxy/logging.ini` and `logging-debug.ini`.
5. If main must push commands to it, register an `on_cmd` callback and have
   main call `self.channel.send_cmd("<new-role>", {...})`.
