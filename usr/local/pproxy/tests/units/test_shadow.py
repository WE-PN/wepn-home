import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from sqlalchemy.exc import DatabaseError

# Setup paths
up_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)) + '/../../')
if up_dir not in sys.path:
    sys.path.append(up_dir)

import shadow  # noqa: E402


class TestShadow(unittest.TestCase):

    def setUp(self):
        self.mock_logger = MagicMock()
        # Mocking Device and WPDiag used in Shadow.__init__
        with patch('shadow.WPDiag'), patch('shadow.MetricsClient'), patch('shadow.Service.is_enabled', return_value=True):
            self.shadow_service = shadow.Shadow(self.mock_logger)

        # Setup common config mock
        self.shadow_service.config = MagicMock()
        self.shadow_service.config.get.side_effect = lambda section, key: {
            ('shadow', 'db-path'): '/tmp/test_shadow.db',
            ('shadow', 'server-socket'): '/tmp/test_socket',
            ('shadow', 'method'): 'aes-256-gcm'
        }.get((section, key))

    @patch('shadow.dataset.connect')
    @patch('shadow.os.path.isfile', return_value=True)
    def test_corrupted_files_detects_malformed(self, mock_isfile, mock_connect):
        mock_db = MagicMock()
        mock_connect.return_value = mock_db

        # Simulate malformed DB error on count()
        mock_db['servers'].count.side_effect = Exception("database disk image is malformed")

        result = self.shadow_service.corrupted_files()
        self.assertTrue(result)
        self.mock_logger.error.assert_called()

    @patch('shadow.dataset.connect')
    @patch('shadow.os.path.isfile', return_value=True)
    def test_corrupted_files_detects_integrity_failure(self, mock_isfile, mock_connect):
        mock_db = MagicMock()
        mock_connect.return_value = mock_db

        # Simulate integrity check failure
        mock_db.query.return_value = [{'integrity_check': 'Main error'}, {'integrity_check': 'ok'}]

        result = self.shadow_service.corrupted_files()
        self.assertTrue(result)
        self.mock_logger.error.assert_called()

    @patch('shadow.Shadow.corrupted_files', return_value=True)
    @patch('shadow.Shadow.restore', return_value=True)
    def test_self_test_triggers_restore(self, mock_restore, mock_corrupted):
        # We also need to mock the rest of self_test to avoid actual network/commands
        with patch('shadow.Device'), patch('shadow.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            self.shadow_service.self_test()

        mock_restore.assert_called_once()
        self.mock_logger.warning.assert_called_with(
            "Corruption detected in self_test, attempting restore.")

    @patch('shadow.dataset.connect')
    def test_start_all_triggers_restore_on_corruption(self, mock_connect):
        # First call triggers corruption-like error
        mock_connect.side_effect = [
            Exception("database disk image is malformed"),
            MagicMock()  # Second call after restore
        ]

        with patch('shadow.Shadow.restore', return_value=True) as mock_restore:
            self.shadow_service.start_all()

        mock_restore.assert_called_once()
        self.mock_logger.warning.assert_called_with(
            "Database corruption detected in start_all, attempting restore.")

    @patch('shadow.Shadow.corrupted_files', return_value=True)
    def test_backup_skips_if_corrupted(self, mock_corrupted):
        with patch('shadow.shutil.copyfile') as mock_copy:
            result = self.shadow_service.backup()
            self.assertFalse(result)
            mock_copy.assert_not_called()
            self.mock_logger.error.assert_called_with(
                "Skipping backup because database is corrupted.")

    @patch('shadow.os.path.isfile', return_value=True)
    @patch('shadow.shutil.copyfile')
    @patch('shadow.Shadow.corrupted_files')
    def test_restore_verifies_health(self, mock_corrupted, mock_copy, mock_isfile):
        # Restore success, then check health: still corrupted
        mock_corrupted.return_value = True

        result = self.shadow_service.restore()

        self.assertFalse(result)
        self.mock_logger.critical.assert_called_with("Database still corrupted after restoration!")

    @patch('shadow.Shadow.corrupted_files', return_value=False)
    @patch('shadow.Shadow.db_changed', return_value=True)
    @patch('shadow.Shadow.backup', return_value=True)
    def test_backup_restore_performs_backup_if_healthy_and_changed(self, mock_backup, mock_changed, mock_corrupted):
        self.shadow_service.backup_restore()
        mock_backup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
