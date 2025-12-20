import service
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock EXTERNAL dependencies only
for m in ['getmac', 'pystemd', 'pystemd.systemd1', 'distro', 'netifaces', 'psutil', 'upnpclient', 'packaging', 'packaging.version', 'qrcode', 'Adafruit_SSD1306', 'adafruit_rgb_display', 'board', 'sqlalchemy', 'sqlalchemy.exc']:
    if m not in sys.modules:
        sys.modules[m] = MagicMock()

# Import Service
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)


class TestServiceSchedule(unittest.TestCase):
    @patch('service.configparser')
    @patch('service.WStatus')
    def setUp(self, mock_wstatus, mock_configparser):
        self.service = service.Service("test_service", MagicMock())

    def test_initialization(self):
        self.assertEqual(self.service.scheduled_times, {})

    def test_add_scheduled_time(self):
        self.service.add_scheduled_time(0, [10, 11, 12])
        self.assertEqual(self.service.scheduled_times[0], [10, 11, 12])

        # Add more hours, check dedup and sort
        self.service.add_scheduled_time(0, [11, 9])
        self.assertEqual(self.service.scheduled_times[0], [9, 10, 11, 12])

    @patch('service.datetime')
    def test_is_currently_scheduled(self, mock_datetime):
        # Case 1: No schedule -> Always True
        self.assertTrue(self.service.is_currently_scheduled())

        # Add schedule: Monday (0) 10:00
        self.service.add_scheduled_time(0, [10])

        # Case 2: Match
        # Mock datetime.now() to return Monday 10:30
        # weekday() is 0 for Monday
        mock_now = MagicMock()
        mock_now.weekday.return_value = 0
        mock_now.hour = 10
        mock_datetime.now.return_value = mock_now

        self.assertTrue(self.service.is_currently_scheduled())

        # Case 3: Wrong hour
        mock_now.hour = 11
        self.assertFalse(self.service.is_currently_scheduled())

        # Case 4: Wrong day
        mock_now.weekday.return_value = 1  # Tuesday
        mock_now.hour = 10
        self.assertFalse(self.service.is_currently_scheduled())


if __name__ == '__main__':
    unittest.main()
