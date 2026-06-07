import unbounded
import unittest
from unittest.mock import MagicMock, patch, call
import json
import os
import sys

# Add the project root to sys.path
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)


class TestUnbounded(unittest.TestCase):

    @patch('service.Service.__init__', return_value=None)
    @patch('atexit.register')
    def setUp(self, mock_atexit, mock_service_init):
        self.mock_logger = MagicMock()
        # Mock add_scheduled_time during init to avoid AttributeError on scheduled_times
        with patch('unbounded.Unbounded.add_scheduled_time'):
            with patch('unbounded.IPW'):
                self.ub = unbounded.Unbounded(self.mock_logger)

        # Manually initialize attributes that would be set by Service.__init__
        self.ub.scheduled_times = {}
        self.ub.name = "unbounded"
        self.ub.logger = self.mock_logger

        # Verify Service init call
        mock_service_init.assert_called_with("unbounded", self.mock_logger)
        # Verify atexit registration
        mock_atexit.assert_called_with(self.ub.cleanup)

    def test_init(self):
        self.assertEqual(self.ub.system_service_name, "wepn-unbounded.service")

        # Since we mocked add_scheduled_time in setUp, we should test the logic here
        # or by not mocking it if we initialize scheduled_times before Unbounded(..)
        # Let's just manully call it to verify it works as expected (it's inherited from Service)
        from service import Service
        self.ub.add_scheduled_time = Service.add_scheduled_time.__get__(
            self.ub, unbounded.Unbounded)

        for day in range(5):
            self.ub.add_scheduled_time(day, [1, 2, 3])
            self.assertIn(1, self.ub.scheduled_times[day])
            self.assertIn(2, self.ub.scheduled_times[day])
            self.assertIn(3, self.ub.scheduled_times[day])

    def test_get_limit(self):
        with patch.object(self.ub, 'get_overlayable_config_value') as mock_get:
            mock_get.return_value = "50kbit"
            self.assertEqual(self.ub.get_limit(), "50kbit")
            mock_get.assert_called_with("bw-limit", "28kbit")

    def test_is_enabled(self):
        self.ub.wstatus = MagicMock()
        with patch.object(self.ub, 'get_overlayable_config_value') as mock_get:
            # Case 1: Claimed and enabled
            self.ub.wstatus.get.return_value = '1'
            mock_get.return_value = True
            self.assertTrue(self.ub.is_enabled())

            # Case 2: Claimed but disabled
            mock_get.return_value = False
            self.assertFalse(self.ub.is_enabled())

            # Case 3: Unclaimed but enabled
            self.ub.wstatus.get.return_value = '0'
            mock_get.return_value = True
            self.assertFalse(self.ub.is_enabled())

    def test_is_kindness_mode(self):
        self.assertTrue(self.ub.is_kindness_mode())

    def test_cleanup_and_clear(self):
        with patch.object(self.ub, 'clear') as mock_clear:
            self.ub.cleanup()
            mock_clear.assert_called_once()

    @patch('unbounded.Device')
    def test_start_all(self, MockDevice):
        mock_dev = MockDevice.return_value
        self.ub.config = MagicMock()
        self.ub.config.get.return_value = "eth0"
        with patch.object(self.ub, 'get_limit', return_value="28kbit"):
            self.ub.start_all()

            # Verify bandwidth limit command
            mock_dev.execute_setuid.assert_any_call("1 23 set unbounded eth0 28kbit")
            # Verify start service command
            mock_dev.execute_setuid.assert_any_call("0 6 1")

    @patch('unbounded.Device')
    def test_stop_all(self, MockDevice):
        mock_dev = MockDevice.return_value
        self.ub.stop_all()
        mock_dev.execute_setuid.assert_called_with("0 6 0")

    def test_basic_controls(self):
        with patch.object(self.ub, 'start_all') as mock_start, \
                patch.object(self.ub, 'stop_all') as mock_stop:

            self.ub.start()
            mock_start.assert_called_once()

            mock_start.reset_mock()
            self.ub.stop()
            mock_stop.assert_called_once()

            mock_stop.reset_mock()
            self.ub.restart()
            mock_stop.assert_called_once()
            mock_start.assert_called_once()

            self.ub.reload()  # Should do nothing

    def test_self_test(self):
        self.assertTrue(self.ub.self_test())

    def test_get_config_settings(self):
        with patch.object(self.ub, 'is_enabled', return_value=True), \
                patch.object(self.ub, 'get_limit', return_value="28kbit"):

            settings = self.ub.get_config_settings()
            self.assertEqual(settings["name"], "unbounded")
            self.assertEqual(settings["settings"]["enabled"], True)
            self.assertEqual(settings["settings"]["bw-limit"], "28kbit")

    def test_configure(self):
        self.ub.service_config = MagicMock()
        self.ub.recover_missing_servers = MagicMock()
        self.ub.self_test = MagicMock()

        conf = {"bw-limit": "100kbit"}
        with patch.object(self.ub, 'get_limit', side_effect=['100kbit', '100kbit']):
            self.ub.configure(conf)

        self.ub.service_config.set_service_config.assert_called_with("unbounded", conf)
        self.ub.service_config.save.assert_called()
        self.ub.self_test.assert_called_once()
        self.ub.recover_missing_servers.assert_called_once()

    def test_configure_applies_bw_limit_when_changed(self):
        self.ub.service_config = MagicMock()
        self.ub.recover_missing_servers = MagicMock()
        self.ub.self_test = MagicMock()

        with patch.object(self.ub, 'get_limit', side_effect=['28kbit', '10kbit']), \
                patch('unbounded.Device') as mock_device:
            self.ub.configure({'bw-limit': '10kbit'})

        mock_device.return_value.execute_setuid.assert_called_once_with(
            '1 23 set unbounded eth0 10kbit'
        )

    def test_configure_skips_bw_limit_when_unchanged(self):
        self.ub.service_config = MagicMock()
        self.ub.recover_missing_servers = MagicMock()
        self.ub.self_test = MagicMock()

        with patch.object(self.ub, 'get_limit', side_effect=['28kbit', '28kbit']), \
                patch('unbounded.Device') as mock_device:
            self.ub.configure({'bw-limit': '28kbit'})

        mock_device.return_value.execute_setuid.assert_not_called()

    def test_get_usage_status_summary(self):
        self.assertEqual(self.ub.get_usage_status_summary(), {"unbounded": 1})

    @patch('unbounded.Device')
    def test_clear_usage_counters(self, MockDevice):
        mock_dev = MockDevice.return_value
        self.ub.config = MagicMock()
        self.ub.config.get.return_value = "eth0"

        mock_dev.execute_cmd_output.return_value = (b"", b"", False, None)

        self.ub.clear_usage_counters()

        # SRUN + " 1 25 unbounded eth0"
        # Since we don't know SRUN's value exactly without checking, let's see.
        # In unbounded.py: from device import SRUN as SRUN

        mock_dev.execute_cmd_output.assert_called()
        args, _ = mock_dev.execute_cmd_output.call_args
        self.assertIn("1 25 unbounded eth0", args[0])

    @patch('unbounded.Device')
    def test_get_usage_deltas(self, MockDevice):
        mock_dev = MockDevice.return_value
        self.ub.config = MagicMock()
        self.ub.config.get.return_value = "eth0"

        # Case 1: Success
        mock_dev.execute_cmd_output.return_value = (b"100\n", b"", False, MagicMock())
        result = self.ub.get_usage_deltas(clear_counters=True)
        self.assertEqual(result, {"unbounded": 800})  # 100 * 8

        # Case 2: Failure
        mock_dev.execute_cmd_output.side_effect = Exception("error")
        result = self.ub.get_usage_deltas()
        self.assertEqual(result, {"unbounded": 0})


if __name__ == '__main__':
    unittest.main()
