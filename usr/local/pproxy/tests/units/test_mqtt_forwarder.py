import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                'system_services'))

from mqtt_forwarder import MQTTForwarder, rc_to_int  # noqa: E402


class TestMQTTForwarder(unittest.TestCase):

    def setUp(self):
        self.patcher_cp = patch('mqtt_forwarder.configparser.ConfigParser')
        self.patcher_wstatus = patch('mqtt_forwarder.WStatus')
        self.patcher_channel = patch('mqtt_forwarder.ChannelClient')
        self.mock_cp = self.patcher_cp.start()
        self.mock_wstatus_cls = self.patcher_wstatus.start()
        self.mock_channel_cls = self.patcher_channel.start()
        for p in (self.patcher_cp, self.patcher_wstatus, self.patcher_channel):
            self.addCleanup(p.stop)

        cfg = {
            ('mqtt', 'username'): 'testuser',
            ('mqtt', 'password'): 'testpass',
            ('mqtt', 'host'): 'mqtt.test',
            ('mqtt', 'port'): '8883',
            ('mqtt', 'timeout'): '30',
        }
        self.mock_cp.return_value.get.side_effect = \
            lambda s, o: cfg.get((s, o), '')

        self.fwd = MQTTForwarder(logger=MagicMock())

    # --- claimed gate ---

    def test_wait_until_claimed_returns_when_claimed(self):
        self.fwd.status.get.return_value = '1'
        self.fwd.wait_until_claimed()  # must return without sleeping
        self.fwd.status.reload.assert_called()

    def test_wait_until_claimed_sleeps_until_claimed(self):
        self.fwd.status.get.side_effect = ['0', '0', '1']
        with patch('mqtt_forwarder.time.sleep') as mock_sleep:
            self.fwd.wait_until_claimed()
        self.assertEqual(mock_sleep.call_count, 2)

    # --- status writes ---

    def test_set_mqtt_status_reload_set_save(self):
        self.fwd.set_mqtt_status(1, 0)
        self.fwd.status.reload.assert_called_once()
        self.fwd.status.set.assert_any_call('mqtt', 1)
        self.fwd.status.set.assert_any_call('mqtt-reason', 0)
        self.fwd.status.save.assert_called_once()

    # --- on_connect / on_disconnect ---

    def test_on_connect_subscribes_and_reports(self):
        client = MagicMock()
        self.fwd.on_connect(client, None, None, 0)
        client.subscribe.assert_called_once_with('devices/testuser/#', qos=1)
        self.fwd.status.set.assert_any_call('mqtt', 1)
        self.fwd.channel.send_state.assert_called_once_with(1, 0)

    def test_on_disconnect_reports_down(self):
        self.fwd.on_disconnect(MagicMock(), None, None, 5)
        self.fwd.status.set.assert_any_call('mqtt', 0)
        self.fwd.status.set.assert_any_call('mqtt-reason', 5)
        self.fwd.channel.send_state.assert_called_once_with(0, 5)

    # --- rc_to_int (paho v2 ReasonCode objects) ---

    def test_rc_to_int_plain_int(self):
        self.assertEqual(rc_to_int(7), 7)

    def test_rc_to_int_reason_code_object(self):
        class FakeReasonCode:
            value = 135
        self.assertEqual(rc_to_int(FakeReasonCode()), 135)

    def test_rc_to_int_unconvertible_falls_back_to_zero(self):
        self.assertEqual(rc_to_int(None), 0)

    def test_on_disconnect_converts_reason_code_object(self):
        class FakeReasonCode:
            value = 7
        self.fwd.on_disconnect(MagicMock(), None, None, FakeReasonCode())
        self.fwd.status.set.assert_any_call('mqtt-reason', 7)
        self.fwd.channel.send_state.assert_called_once_with(0, 7)

    # --- on_message ---

    def _mqtt_msg(self, payload_bytes):
        msg = MagicMock()
        msg.topic = 'devices/testuser/1'
        msg.payload = payload_bytes
        return msg

    def test_on_message_forwards_valid_payload(self):
        payload = {'action': 'reboot_device'}
        self.fwd.channel.send_msg.return_value = {'type': 'ack',
                                                  'status': 'queued'}
        self.fwd.on_message(None, None, self._mqtt_msg(json.dumps(payload).encode()))
        args, kwargs = self.fwd.channel.send_msg.call_args
        self.assertEqual(args[0], payload)

    def test_on_message_drops_malformed_json(self):
        self.fwd.on_message(None, None, self._mqtt_msg(b'not valid json'))
        self.fwd.channel.send_msg.assert_not_called()

    def test_on_message_drops_non_object_payload(self):
        self.fwd.on_message(None, None, self._mqtt_msg(b'"just a string"'))
        self.fwd.channel.send_msg.assert_not_called()

    def test_on_message_unacked_only_warns(self):
        self.fwd.channel.send_msg.return_value = None
        self.fwd.on_message(None, None,
                            self._mqtt_msg(b'{"action": "start_service"}'))
        self.fwd.logger.warning.assert_called()


if __name__ == '__main__':
    unittest.main()
