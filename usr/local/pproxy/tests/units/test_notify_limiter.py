import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

#autopep8: off
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)
import constants
from notify_limiter import NOTIFY_SECTION, NotificationLimiter
from wstatus import WStatus
#autopep8: on


class TestNotificationLimiter(unittest.TestCase):

    def setUp(self):
        fd, self.tmp_path = tempfile.mkstemp()
        os.close(fd)
        self.logger = MagicMock()

    def tearDown(self):
        try:
            os.unlink(self.tmp_path)
        except OSError:
            pass

    def _make_limiter(self):
        status = WStatus(self.logger, source_file=self.tmp_path)
        return NotificationLimiter(self.logger, status), status

    def test_first_allow_true_and_persisted(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))

        status2 = WStatus(self.logger, source_file=self.tmp_path)
        self.assertTrue(status2.has_section(NOTIFY_SECTION))

    def test_repeat_within_cooldown_is_denied(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))
        with patch('notify_limiter.time.time', return_value=1500):
            self.assertFalse(limiter.allow('add_email', 'a@b.com', 3600))

    def test_allow_after_cooldown_expires(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))
        with patch('notify_limiter.time.time', return_value=1000 + 3601):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))

    def test_dedup_same_token_after_cooldown_still_denied(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600, token='1.2.3.4'))
        with patch('notify_limiter.time.time', return_value=1000 + 3601):
            self.assertFalse(limiter.allow('add_email', 'a@b.com', 3600, token='1.2.3.4'))

    def test_changed_token_after_cooldown_allowed(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600, token='1.2.3.4'))
        with patch('notify_limiter.time.time', return_value=1000 + 3601):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600, token='5.6.7.8'))

    def test_zero_cooldown_still_counts_toward_daily_cap(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            for i in range(constants.NOTIFY_DAILY_LIMIT):
                self.assertTrue(limiter.allow('add_email', 'user%d@b.com' % i, 0))
            self.assertFalse(limiter.allow('add_email', 'overflow@b.com', 0))

    def test_daily_cap_resets_after_24h(self):
        limiter, _status = self._make_limiter()
        t = 1000
        with patch('notify_limiter.time.time', return_value=t):
            for i in range(constants.NOTIFY_DAILY_LIMIT):
                limiter.allow('add_email', 'user%d@b.com' % i, 0)
            self.assertFalse(limiter.allow('add_email', 'overflow@b.com', 0))
        with patch('notify_limiter.time.time', return_value=t + 86400 + 1):
            self.assertTrue(limiter.allow('add_email', 'overflow@b.com', 0))

    def test_persistence_survives_restart(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))

        status2 = WStatus(self.logger, source_file=self.tmp_path)
        limiter2 = NotificationLimiter(self.logger, status2)
        with patch('notify_limiter.time.time', return_value=1500):
            self.assertFalse(limiter2.allow('add_email', 'a@b.com', 3600))

    def test_clock_backwards_allowed_with_warning_and_window_reset(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=10000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))
        with patch('notify_limiter.time.time', return_value=100):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))
        self.logger.warning.assert_called()

    def test_pruning_drops_entries_past_ttl(self):
        limiter, status = self._make_limiter()
        old_key = limiter._key('add_email', 'old@b.com')
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'old@b.com', 0))
        with patch('notify_limiter.time.time',
                   return_value=1000 + constants.NOTIFY_ENTRY_TTL_SECONDS + 1):
            self.assertTrue(limiter.allow('add_email', 'new@b.com', 0))
        self.assertFalse(status.has_option(NOTIFY_SECTION, old_key))

    def test_pruning_bounds_max_entries(self):
        limiter, status = self._make_limiter()
        total_needed = constants.NOTIFY_MAX_ENTRIES + 5
        added = 0
        day = 0
        while added < total_needed:
            t = 1000 + day * 86400
            with patch('notify_limiter.time.time', return_value=t):
                for _ in range(constants.NOTIFY_DAILY_LIMIT):
                    if added >= total_needed:
                        break
                    self.assertTrue(limiter.allow('add_email', 'user%d@b.com' % added, 0))
                    added += 1
            day += 1

        entry_count = len([
            opt for opt in status.status.options(NOTIFY_SECTION)
            if opt not in ('daily_window_start', 'daily_count')
        ])
        self.assertLessEqual(entry_count, constants.NOTIFY_MAX_ENTRIES)

    def test_kinds_independent_per_recipient(self):
        limiter, _status = self._make_limiter()
        with patch('notify_limiter.time.time', return_value=1000):
            self.assertTrue(limiter.allow('add_email', 'a@b.com', 3600))
            self.assertTrue(limiter.allow('add_email', 'c@d.com', 3600))
            self.assertTrue(limiter.allow('user_added_msg', 'a@b.com', 3600))


if __name__ == '__main__':
    unittest.main()
