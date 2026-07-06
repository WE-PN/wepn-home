import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                'system_services'))

from message_poller import MessagePoller  # noqa: E402


class TestMessagePoller(unittest.TestCase):

    def setUp(self):
        self.patcher_wstatus = patch('message_poller.WStatus')
        self.patcher_channel = patch('message_poller.ChannelClient')
        self.patcher_messages = patch('message_poller.Messages')
        self.mock_wstatus_cls = self.patcher_wstatus.start()
        self.mock_channel_cls = self.patcher_channel.start()
        self.mock_messages_cls = self.patcher_messages.start()
        for p in (self.patcher_wstatus, self.patcher_channel,
                  self.patcher_messages):
            self.addCleanup(p.stop)

        self.poller = MessagePoller(logger=MagicMock())
        self.messages = self.mock_messages_cls.return_value

    def _backend_message(self, msg_id, body=None):
        return {'id': msg_id,
                'message_body': body or {'action': 'start_service'}}

    # --- claimed gate ---

    def test_wait_until_claimed_returns_when_claimed(self):
        self.poller.status.get.return_value = '1'
        self.poller.wait_until_claimed()
        self.poller.status.reload.assert_called()

    def test_wait_until_claimed_sleeps_until_claimed(self):
        self.poller.status.get.side_effect = ['0', '1']
        with patch('message_poller.time.sleep') as mock_sleep:
            self.poller.wait_until_claimed()
        self.assertEqual(mock_sleep.call_count, 1)

    # --- fetch-now command ---

    def test_on_cmd_fetch_now_sets_event(self):
        self.assertFalse(self.poller.fetch_event.is_set())
        self.poller.on_cmd({'cmd': 'fetch-now'})
        self.assertTrue(self.poller.fetch_event.is_set())

    def test_on_cmd_unknown_ignored(self):
        self.poller.on_cmd({'cmd': 'something-else'})
        self.assertFalse(self.poller.fetch_event.is_set())
        self.poller.on_cmd('not a dict')
        self.assertFalse(self.poller.fetch_event.is_set())

    # --- poll_once ---

    def test_poll_marks_read_only_after_ack(self):
        self.messages.get_messages.return_value = [self._backend_message(11)]
        self.poller.channel.send_msg.return_value = {'type': 'ack',
                                                     'status': 'queued'}
        self.poller.poll_once()
        args, kwargs = self.poller.channel.send_msg.call_args
        self.assertEqual(args[0], {'action': 'start_service'})
        self.assertEqual(kwargs.get('ref'), 11)
        self.assertFalse(kwargs.get('replay'))
        self.messages.mark_msg_read.assert_called_once_with(11)
        self.assertIn(11, self.poller.forwarded_ids)

    def test_poll_no_ack_leaves_unread(self):
        self.messages.get_messages.return_value = [self._backend_message(12)]
        self.poller.channel.send_msg.return_value = None
        self.poller.poll_once()
        self.messages.mark_msg_read.assert_not_called()
        self.assertNotIn(12, self.poller.forwarded_ids)

    def test_poll_discarded_payload_still_marked_read(self):
        # a payload main rejects must not be refetched forever
        self.messages.get_messages.return_value = [
            self._backend_message(13, body={'bogus': True})]
        self.poller.channel.send_msg.return_value = {'type': 'ack',
                                                     'status': 'discarded'}
        self.poller.poll_once()
        self.messages.mark_msg_read.assert_called_once_with(13)

    def test_poll_skips_recently_forwarded_ids(self):
        self.poller.forwarded_ids.append(14)
        self.messages.get_messages.return_value = [self._backend_message(14)]
        self.poller.poll_once()
        self.poller.channel.send_msg.assert_not_called()
        self.messages.mark_msg_read.assert_not_called()

    def test_poll_creates_fresh_messages_instance(self):
        self.messages.get_messages.return_value = []
        self.poller.poll_once()
        self.poller.poll_once()
        self.assertEqual(self.mock_messages_cls.call_count, 2)


if __name__ == '__main__':
    unittest.main()
