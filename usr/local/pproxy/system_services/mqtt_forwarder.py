import json
import logging
import os
import ssl
import sys
import time
from logging import config  # noqa

import paho.mqtt.client as mqtt

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from constants import CLAIMED_RECHECK_SECONDS, LOG_CONFIG  # noqa E402 need up_dir first
from msg_channel import ChannelClient  # noqa E402
from wstatus import WStatus  # noqa E402

try:
    from configparser import configparser
except ImportError:
    import configparser

CONFIG_FILE = '/etc/pproxy/config.ini'
CONNECT_RETRY_SECONDS = 60
# don't stall the paho network loop waiting for the hub;
# unacked frames are replayed by the channel client on reconnect
SEND_TIMEOUT_SECONDS = 2

logging.config.fileConfig(LOG_CONFIG, disable_existing_loggers=False)


def rc_to_int(reason_code):
    # paho v2 callbacks pass ReasonCode objects; status.ini and state
    # frames carry plain ints
    try:
        return int(getattr(reason_code, "value", reason_code))
    except (TypeError, ValueError):
        return 0


class MQTTForwarder():
    """Owns the broker connection; forwards every command payload to the
    main pproxy process over the local message channel."""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("mqtt_forwarder")
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.status = WStatus(self.logger)
        self.channel = ChannelClient("mqtt", logger=self.logger)

    def wait_until_claimed(self):
        while True:
            self.status.reload()
            if str(self.status.get('claimed')) == "1":
                return
            self.logger.debug("device not claimed, mqtt forwarder idle")
            time.sleep(CLAIMED_RECHECK_SECONDS)

    def set_mqtt_status(self, connected, reason):
        self.status.reload()
        self.status.set('mqtt', connected)
        self.status.set('mqtt-reason', reason)
        self.status.save()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.logger.info("Connected with result code " + str(reason_code))
        # subscribing in on_connect() means subscriptions are renewed on reconnect
        topic = "devices/" + self.config.get('mqtt', 'username') + "/#"
        self.logger.info('subscribing to: ' + topic)
        client.subscribe(topic, qos=1)
        rc = rc_to_int(reason_code)
        self.set_mqtt_status(1, rc)
        self.channel.send_state(1, rc)

    def on_disconnect(self, client, userdata, disconnect_flags,
                      reason_code, properties=None):
        self.logger.info("MQTT disconnected")
        rc = rc_to_int(reason_code)
        self.set_mqtt_status(0, rc)
        self.channel.send_state(0, rc)

    def on_message(self, client, userdata, msg):
        # never log the payload: message bodies can carry access links, cert
        # names and emails, and the log lands in error.log which is readable by
        # the wepn-api group. log only non-sensitive metadata.
        self.logger.debug("received mqtt message on " + str(msg.topic))
        try:
            data = json.loads(msg.payload)
        except (ValueError, TypeError, UnicodeDecodeError):
            self.logger.error("dropping malformed mqtt payload (%d bytes)"
                              % len(msg.payload or b""))
            return
        if not isinstance(data, dict):
            self.logger.error("dropping non-object mqtt payload of type "
                              + type(data).__name__)
            return
        ack = self.channel.send_msg(data, timeout=SEND_TIMEOUT_SECONDS)
        if ack is None:
            self.logger.warning("main process not reachable, payload buffered for replay")

    def build_client(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             self.config.get('mqtt', 'username'), clean_session=False)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        client.tls_set("/etc/ssl/certs/ISRG_Root_X1.pem",
                       tls_version=ssl.PROTOCOL_TLSv1_2)
        client.username_pw_set(username=self.config.get('mqtt', 'username'),
                               password=self.config.get('mqtt', 'password'))
        return client

    def run(self):
        self.wait_until_claimed()
        self.channel.start()
        client = self.build_client()
        while True:
            try:
                self.logger.debug("mqtt host: " + str(self.config.get('mqtt', 'host')))
                client.connect(str(self.config.get('mqtt', 'host')),
                               int(self.config.get('mqtt', 'port')),
                               int(self.config.get('mqtt', 'timeout')))
                # blocking call: processes traffic and handles broker reconnects
                client.loop_forever()
            except Exception as error:
                self.logger.error("MQTT connect failed: " + str(error))
                self.set_mqtt_status(0, 0)
                time.sleep(CONNECT_RETRY_SECONDS)


if __name__ == "__main__":
    MQTTForwarder().run()
