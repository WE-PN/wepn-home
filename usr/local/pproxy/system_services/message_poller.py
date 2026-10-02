import logging
import os
import random
import sys
import threading
import time
from collections import deque
from logging import config  # noqa

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from constants import (CLAIMED_RECHECK_SECONDS,  # noqa E402 need up_dir first
                       LOG_CONFIG,
                       MESSAGE_POLL_INTERVAL_SECONDS,
                       MESSAGE_POLL_JITTER_SECONDS)
from messages import Messages  # noqa E402
from msg_channel import ChannelClient  # noqa E402
from wstatus import WStatus  # noqa E402

SEND_TIMEOUT_SECONDS = 10
RECENT_IDS_KEPT = 200

logging.config.fileConfig(LOG_CONFIG, disable_existing_loggers=False)


class MessagePoller():
    """Periodically fetches commands from the backend messaging API and
    forwards them to the main pproxy process over the local message channel.
    Messages are marked read on the backend only after main acknowledges
    them, so anything unacked is redelivered by the next poll."""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("message_poller")
        self.status = WStatus(self.logger)
        self.fetch_event = threading.Event()
        self.channel = ChannelClient("poller", on_cmd=self.on_cmd,
                                     logger=self.logger)
        self.forwarded_ids = deque(maxlen=RECENT_IDS_KEPT)

    def on_cmd(self, cmd):
        if isinstance(cmd, dict) and cmd.get("cmd") == "fetch-now":
            self.logger.debug("fetch-now received")
            self.fetch_event.set()

    def wait_until_claimed(self):
        while True:
            self.status.reload()
            if str(self.status.get('claimed')) == "1":
                return
            self.logger.debug("device not claimed, message poller idle")
            time.sleep(CLAIMED_RECHECK_SECONDS)

    def poll_once(self):
        # fresh instance every cycle so config/status (e.g. e2e_key) are current
        messages = Messages(logger=self.logger)
        for message in messages.get_messages():
            msg_id = int(message["id"])
            if msg_id in self.forwarded_ids:
                continue
            ack = self.channel.send_msg(message["message_body"], ref=msg_id,
                                        timeout=SEND_TIMEOUT_SECONDS, replay=False)
            if ack is not None and ack.get("type") == "ack":
                # delivered: mark read even if main discarded the payload as
                # invalid, otherwise a bad message would be refetched forever
                self.forwarded_ids.append(msg_id)
                messages.mark_msg_read(msg_id)
                self.logger.info("message " + str(msg_id) + " forwarded, status: "
                                 + str(ack.get("status")))
            else:
                # left unread on the backend; next poll retries it
                self.logger.warning("message not acked, will retry: " + str(msg_id))

    def run(self):
        self.wait_until_claimed()
        self.channel.start()
        while True:
            try:
                self.poll_once()
            except Exception:
                self.logger.exception("poll cycle failed")
            wait = MESSAGE_POLL_INTERVAL_SECONDS + \
                random.uniform(0, MESSAGE_POLL_JITTER_SECONDS)  # nosec B311 not crypto
            self.fetch_event.wait(wait)
            self.fetch_event.clear()


if __name__ == "__main__":
    MessagePoller().run()
