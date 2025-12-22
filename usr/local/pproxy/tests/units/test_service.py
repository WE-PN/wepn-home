import service
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call
import json

# Mock EXTERNAL dependencies only
for m in ['getmac', 'pystemd', 'pystemd.systemd1', 'distro', 'netifaces', 'psutil', 'upnpclient', 'packaging', 'packaging.version', 'qrcode', 'Adafruit_SSD1306', 'adafruit_rgb_display', 'board', 'sqlalchemy', 'sqlalchemy.exc']:
    if m not in sys.modules:
        sys.modules[m] = MagicMock()

# Import Service
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)


class TestService(unittest.TestCase):

    @patch('service.configparser.ConfigParser')
    @patch('service.WStatus')
    def setUp(self, mock_wstatus_class, mock_configparser_class):
        self.mock_logger = MagicMock()
        self.service_name = "test_service"

        # Setup mocks
        self.mock_config = MagicMock()
        mock_configparser_class.return_value = self.mock_config

        self.mock_wstatus_global = MagicMock()
        self.mock_wstatus_service = MagicMock()
        # WStatus is instantiated twice: once for global, once for service specific
        mock_wstatus_class.side_effect = [self.mock_wstatus_global, self.mock_wstatus_service]

        self.service = service.Service(self.service_name, self.mock_logger)

        # Reset side_effect for other tests that might instantiate Service
        mock_wstatus_class.side_effect = None

    def test_init(self):
        self.assertEqual(self.service.name, self.service_name)
        self.assertEqual(self.service.logger, self.mock_logger)
        # Verify config read
        self.mock_config.read.assert_called()
        # Verify WStatus instantiations
        self.assertTrue(self.service.wstatus)
        self.assertTrue(self.service.service_config)

    def test_is_kindness_mode(self):
        self.assertFalse(self.service.is_kindness_mode())

    def test_basic_methods(self):
        # Test methods that currently just return False, None or pass
        self.assertFalse(self.service.add_user("cert", "ip", "pw", 8080, "en"))
        self.assertIsNone(self.service.delete_user("cert"))
        self.assertIsNone(self.service.start())
        self.assertIsNone(self.service.stop())
        self.assertIsNone(self.service.reload())

        # restart calls stop and start
        with patch.object(self.service, 'stop_all', create=True) as m_stop, \
                patch.object(self.service, 'start_all', create=True) as m_start:
            self.service.restart()
            m_stop.assert_called_once()
            m_start.assert_called_once()

    def test_get_config_section_name(self):
        self.assertEqual(self.service.get_config_section_name(), "test_service")

        # Test shadowsocks special case
        ss_service = service.Service("shadowsocks", self.mock_logger)
        self.assertEqual(ss_service.get_config_section_name(), "shadow")

    def test_is_enabled(self):
        # Case 1: Config has section, enabled=1, service active
        self.mock_config.has_section.return_value = True
        self.mock_config.get.return_value = '1'
        self.service.service_config = MagicMock()
        self.service.service_config.get_service_status.return_value = True

        self.assertTrue(self.service.is_enabled())

        # Case 2: Config has section, enabled=0
        self.mock_config.get.return_value = '0'
        self.assertFalse(self.service.is_enabled())

        # Case 3: Config missing section
        self.mock_config.has_section.return_value = False
        self.assertFalse(self.service.is_enabled())

    def test_set_enabled(self):
        # Case 1: Enabling (was disabled)
        with patch.object(self.service, 'is_enabled') as m_is_enabled:
            m_is_enabled.side_effect = [False, True]  # First call check (False), subsequent logic
            self.service.service_config = MagicMock()

            with patch.object(self.service, 'start') as m_start:
                self.service.set_enabled(True)

                self.service.service_config.set_service_status.assert_called_with(
                    "test_service", True)
                self.service.service_config.save.assert_called()
                m_start.assert_called_once()

        # Case 2: Disabling (was enabled)
        with patch.object(self.service, 'is_enabled') as m_is_enabled:
            m_is_enabled.side_effect = [True, False]
            self.service.service_config = MagicMock()

            with patch.object(self.service, 'stop') as m_stop:
                self.service.set_enabled(False)
                m_stop.assert_called_once()

    def test_can_email(self):
        # Case 1: Section exists, email=1
        self.mock_config.has_section.return_value = True
        self.mock_config.get.return_value = '1'
        self.assertTrue(self.service.can_email())

        # Case 2: Section exists, email=0
        self.mock_config.get.return_value = '0'
        self.assertFalse(self.service.can_email())

        # Case 3: Section missing
        self.mock_config.has_section.return_value = False
        self.assertFalse(self.service.can_email())

    def test_empty_methods(self):
        # Verify placeholders return expected empty values
        self.assertEqual(self.service.get_service_creds_summary("1.2.3.4"), {})
        self.assertEqual(self.service.get_usage_status_summary(), ({}, {}))
        self.assertEqual(self.service.get_usage_daily(), {})
        self.assertEqual(self.service.get_short_link_text("cname", "ip"), "")
        self.assertIsNone(self.service.get_access_link("cname"))
        self.assertTrue(self.service.self_test())
        self.assertTrue(self.service.backup_restore())

        txt, html, att, sub = self.service.get_add_email_text("c", "i", "l")
        self.assertEqual((txt, html, att, sub), ('', '', [], ''))

        txt, html, att, sub = self.service.get_removal_email_text("c", "i")
        self.assertEqual((txt, html, att, sub), ('', '', [], ''))

    @patch('service.Device')
    def test_is_running(self, MockDevice):
        # Case 1: No system_service_name, falls back to is_enabled
        self.service.system_service_name = None
        with patch.object(self.service, 'is_enabled') as m_enabled:
            m_enabled.return_value = True
            self.assertTrue(self.service.is_running())

        # Case 2: With system_service_name
        self.service.system_service_name = "sys_svc"
        mock_dev_instance = MockDevice.return_value
        mock_dev_instance.is_service_active.return_value = True

        self.assertTrue(self.service.is_running())
        MockDevice.assert_called_with(self.mock_logger)
        mock_dev_instance.is_service_active.assert_called_with("sys_svc")

    def test_recover_missing_servers(self):
        self.service.system_service_name = "sys_svc"

        # Case 1: Enabled, not running, scheduled -> Should Start
        with patch.object(self.service, 'is_enabled', return_value=True), \
                patch.object(self.service, 'is_running', return_value=False), \
                patch.object(self.service, 'is_currently_scheduled', return_value=True), \
                patch.object(self.service, 'start_all', create=True) as m_start:

            self.service.recover_missing_servers()
            m_start.assert_called_once()
            self.mock_logger.debug.assert_called()

        # Case 2: Running, not enabled -> Should Stop
        with patch.object(self.service, 'is_enabled', return_value=False), \
                patch.object(self.service, 'is_running', return_value=True), \
                patch.object(self.service, 'stop_all', create=True) as m_stop:

            self.service.recover_missing_servers()
            m_stop.assert_called_once()

    def test_scheduling(self):
        # test_service_schedule.py already covers this heavily, but adding here coverage
        self.service.add_scheduled_time(0, [10])
        self.assertEqual(self.service.scheduled_times[0], [10])

        with patch('service.datetime') as mock_datetime:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 0
            mock_now.hour = 10
            mock_datetime.now.return_value = mock_now

            self.assertTrue(self.service.is_currently_scheduled())

            mock_now.hour = 11
            self.assertFalse(self.service.is_currently_scheduled())

    def test_apply_time_limit(self):
        # Case 1: Not enabled or no schedule -> Do nothing
        with patch.object(self.service, 'is_enabled', return_value=False):
            self.service.apply_time_limit()
            # No mocks to check, just ensuring no crash and no side effects

        # Case 2: Enabled, scheduled, currently scheduled, not running -> Start
        self.service.add_scheduled_time(0, [10])
        with patch.object(self.service, 'is_enabled', return_value=True), \
                patch.object(self.service, 'is_currently_scheduled', return_value=True), \
                patch.object(self.service, 'is_running', return_value=False), \
                patch.object(self.service, 'start') as m_start:

            self.service.apply_time_limit()
            m_start.assert_called_once()

        # Case 3: Enabled, scheduled, NOT currently scheduled, running -> Stop
        with patch.object(self.service, 'is_enabled', return_value=True), \
                patch.object(self.service, 'is_currently_scheduled', return_value=False), \
                patch.object(self.service, 'is_running', return_value=True), \
                patch.object(self.service, 'stop') as m_stop:

            self.service.apply_time_limit()
            m_stop.assert_called_once()

    def test_configure(self):
        self.service.service_config = MagicMock()

        # Test with dict
        conf = {"enabled": True}
        with patch.object(self.service, 'set_enabled') as m_set_enabled:
            self.service.configure(conf)
            m_set_enabled.assert_called_with(True)
            self.service.service_config.set_service_config.assert_called()
            self.service.service_config.save.assert_called()

        # Test with json str
        conf_str = '{"enabled": false}'
        with patch.object(self.service, 'set_enabled') as m_set_enabled:
            self.service.configure(conf_str)
            m_set_enabled.assert_called_with(False)

        # Test Exception
        with patch.object(self.service, 'set_enabled', side_effect=Exception("Boom")):
            self.service.configure(conf)
            self.mock_logger.exception.assert_called()

    def test_get_overlayable_config_value(self):
        # Case 1: Value in service config (highest priority)
        self.service.service_config.has_option.return_value = True
        self.service.service_config.get_field.return_value = "overlay_val"

        val = self.service.get_overlayable_config_value("key", default="def")
        self.assertEqual(val, "overlay_val")

        # Case 2: Value in main config (middle priority)
        self.service.service_config.has_option.return_value = False
        self.mock_config.has_option.return_value = True
        self.mock_config.get.return_value = "main_val"

        val = self.service.get_overlayable_config_value("key", default="def")
        self.assertEqual(val, "main_val")

        # Case 3: Default (lowest priority)
        self.mock_config.has_option.return_value = False
        val = self.service.get_overlayable_config_value("key", default="def")
        self.assertEqual(val, "def")

        # Test type conversion safety
        self.mock_config.has_option.return_value = True
        self.mock_config.get.return_value = "123"
        val = self.service.get_overlayable_config_value("key", default=0)
        self.assertEqual(val, 123)

    def test_safe_convert(self):
        # Int
        self.assertEqual(self.service.safe_convert("123", int), 123)
        # Bool
        self.assertTrue(self.service.safe_convert("true", bool))
        self.assertFalse(self.service.safe_convert("False", bool))
        # None
        self.assertEqual(self.service.safe_convert("val", type(None)), "val")
        # Error
        self.assertEqual(self.service.safe_convert("abc", int), "abc")
        self.mock_logger.error.assert_called()

    def test_execute_setuid(self):
        with patch.object(self.service, 'execute_cmd') as m_exec:
            self.service.execute_setuid("ls")
            m_exec.assert_called()
            args, _ = m_exec.call_args
            self.assertIn("ls", args[0])
            self.assertIn("/usr/local/sbin/wepn-run", args[0])


if __name__ == '__main__':
    unittest.main()
