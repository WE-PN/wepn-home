import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Setup paths
up_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)) + '/../../')
if up_dir not in sys.path:
    sys.path.append(up_dir)

import shadow  # noqa: E402


class TestShadow(unittest.TestCase):

    def setUp(self):
        self.mock_logger = MagicMock()
        # Mocking Device and WPDiag used in Shadow.__init__
        with patch('shadow.WPDiag'), patch('shadow.MetricsClient'), patch('shadow.Service.is_enabled', return_value=True), \
                patch('shadow.Shadow.start'):
            self.shadow_service = shadow.Shadow(self.mock_logger)

        # Setup common config mock
        self.shadow_service.config = MagicMock()
        self.shadow_service.config.get.side_effect = lambda section, key: {
            ('shadow', 'db-path'): '/tmp/test_shadow.db',
            ('shadow', 'server-socket'): '/tmp/test_socket',
            ('shadow', 'method'): 'aes-256-gcm',
            ('shadow', 'enabled'): '1',
            ('usage', 'db-path'): '/tmp/test_usage.db',
            ('hw', 'iface'): 'eth0'
        }.get((section, key))
        self.shadow_service.config.getboolean.return_value = False

    @patch('shadow.dataset.connect')
    @patch('shadow.os.path.isfile', return_value=True)
    def test_corrupted_files_detects_malformed(self, mock_isfile, mock_connect):
        mock_db = MagicMock()
        mock_connect.return_value = mock_db
        mock_db['servers'].count.side_effect = Exception("database disk image is malformed")
        result = self.shadow_service.corrupted_files()
        self.assertTrue(result)
        self.mock_logger.error.assert_called()

    @patch('shadow.dataset.connect')
    @patch('shadow.os.path.isfile', return_value=True)
    def test_corrupted_files_detects_integrity_failure(self, mock_isfile, mock_connect):
        mock_db = MagicMock()
        mock_connect.return_value = mock_db
        mock_db.query.return_value = [{'integrity_check': 'Main error'}, {'integrity_check': 'ok'}]
        result = self.shadow_service.corrupted_files()
        self.assertTrue(result)
        self.mock_logger.error.assert_called()

    @patch('shadow.Shadow.corrupted_files', return_value=True)
    @patch('shadow.Shadow.restore', return_value=True)
    @patch('shadow.time.sleep')
    def test_self_test_triggers_restore(self, mock_sleep, mock_restore, mock_corrupted):
        with patch('shadow.Device'), patch('shadow.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            self.shadow_service.self_test()
        mock_restore.assert_called_once()

    @patch('shadow.dataset.connect')
    @patch('shadow.time.sleep')
    def test_start_all_triggers_restore_on_corruption(self, mock_sleep, mock_connect):
        mock_connect.side_effect = [
            Exception("database disk image is malformed"),
            MagicMock()
        ]
        with patch('shadow.Shadow.restore', return_value=True) as mock_restore:
            self.shadow_service.start_all()
        mock_restore.assert_called_once()

    @patch('shadow.Shadow.corrupted_files', return_value=True)
    def test_backup_skips_if_corrupted(self, mock_corrupted):
        with patch('shadow.shutil.copyfile') as mock_copy:
            result = self.shadow_service.backup()
            self.assertFalse(result)
            mock_copy.assert_not_called()

    @patch('shadow.os.path.isfile', return_value=True)
    @patch('shadow.shutil.copyfile')
    @patch('shadow.Shadow.corrupted_files')
    def test_restore_verifies_health(self, mock_corrupted, mock_copy, mock_isfile):
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

    @patch('shadow.Shadow.db_query')
    def test_get_service_creds_summary_uses_db_query(self, mock_db_query):
        mock_db_query.return_value = [{'certname': 'user1', 'password': 'pw', 'server_port': 8000}]
        creds = self.shadow_service.get_service_creds_summary('1.1.1.1')
        self.assertEqual(len(creds), 1)
        mock_db_query.assert_called_with('servers')

    @patch('shadow.dataset.connect')
    def test_db_query_triggers_restore_on_corruption(self, mock_connect):
        mock_connect.side_effect = [
            Exception("database disk image is malformed"),
            MagicMock()
        ]
        with patch('shadow.Shadow.restore', return_value=True) as mock_restore:
            self.shadow_service.db_query('servers')
        mock_restore.assert_called_once()

    @patch('shadow.Shadow.db_query')
    def test_delete_user_uses_db_query(self, mock_db_query):
        mock_table = MagicMock()
        mock_db_query.return_value = mock_table
        mock_table.find_one.return_value = {'certname': 'user1', 'server_port': 8001}
        with patch('shadow.Device'):
            self.shadow_service.sock = MagicMock()
            self.shadow_service.delete_user('user1')
        self.assertEqual(mock_db_query.call_count, 2)

    @patch('shadow.Shadow.db_query')
    def test_get_max_port_uses_db_query(self, mock_db_query):
        mock_db_query.return_value = [{'max(server_port)': 8010}]
        port = self.shadow_service.get_max_port()
        self.assertEqual(port, 8010)
        mock_db_query.assert_called()

    @patch('shadow.Shadow.db_query')
    @patch('shadow.Shadow.start_server')
    def test_add_user_new_user(self, mock_start_server, mock_db_query):
        mock_table = MagicMock()
        mock_db_query.side_effect = [
            mock_table,  # 1. return_table for find user
            [{'max(server_port)': 8000}],  # 2. Get max port
            mock_table,  # 3. return_table=True
            [{'certname': 'newuser', 'server_port': 8001}]  # 4. all_servers
        ]
        mock_table.find_one.return_value = None
        self.shadow_service.diag.find_next_good_port.return_value = (8001, 0)
        with patch('shadow.Shadow.get_overlayable_config_value', return_value="8000"):
            is_new = self.shadow_service.add_user('newuser', '1.1.1.1', 'pass', 0, 'en')
        self.assertTrue(is_new)
        mock_start_server.assert_called_once()

    @patch('shadow.Shadow.db_query')
    def test_stop_all_calls_socket(self, mock_db_query):
        mock_db_query.return_value = [
            {'server_port': 8001, 'certname': 'user1'},
            {'server_port': 8002, 'certname': 'user2'}
        ]
        self.shadow_service.sock = MagicMock()
        self.shadow_service.stop_all()
        self.assertEqual(self.shadow_service.sock.send.call_count, 2)

    @patch('shadow.Shadow.db_query')
    def test_forward_all_calls_device(self, mock_db_query):
        mock_db_query.return_value = [{'server_port': 8001, 'certname': 'user1'}]
        with patch('shadow.Device') as mock_device_class:
            mock_device = mock_device_class.return_value
            self.shadow_service.forward_all()
            mock_device.open_port.assert_called_once_with(8001, 'ShadowSocks user1')

    @patch('shadow.Shadow.create_link_and_hash')
    @patch('shadow.Shadow.db_query')
    @patch('shadow.IPW')
    def test_get_access_link_success(self, mock_ipw, mock_db_query, mock_create_link):
        mock_ipw.return_value.myip.return_value = '1.1.1.1'
        mock_table = MagicMock()
        mock_db_query.return_value = mock_table
        mock_table.find_one.return_value = {
            'password': 'pw', 'server_port': 8001, 'certname': 'user1'}
        mock_create_link.return_value = ('link123', 'hash456')
        link = self.shadow_service.get_access_link('user1')
        self.assertIn('link123', link)
        self.assertIn('hash456', link)

    @patch('shadow.Device')
    def test_start_server_exception_logging(self, mock_device):
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.send.side_effect = Exception("Socket error")
        with patch('shadow.Shadow.shadow_conf_file_save'):
            self.shadow_service.start_server(
                {'server_port': 8001, 'password': 'pw', 'certname': 'user1'})
        self.mock_logger.error.assert_called()

    @patch('shadow.Shadow.db_query')
    @patch('shadow.Shadow.is_enabled', return_value=True)
    def test_get_usage_status_and_deltas_success(self, mock_enabled, mock_db_query):
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()
        mock_db_query.side_effect = [
            [{'certname': 'user1', 'server_port': 8001}],
            mock_servers_table,
            mock_daily_table
        ]
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 1000}'
        mock_servers_table.find_one.return_value = None
        mock_daily_table.find_one.return_value = None
        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_status_and_deltas()
        self.assertEqual(results['user1'], 1)
        self.assertEqual(deltas['user1'], 1000)

    @patch('shadow.Shadow.db_query')
    def test_get_usage_daily_success(self, mock_db_query):
        mock_db_query.side_effect = [
            [{'certname': 'user1'}],
            [{'certname': 'user1', 'date': '2026-01-01', 'start_usage': 100, 'end_usage': 200}]
        ]
        daily = self.shadow_service.get_usage_daily()
        self.assertEqual(daily['user1'][0]['usage'], 100)

    def test_get_usage_json_success(self):
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 1000}'
        res = self.shadow_service.get_usage_json()
        self.assertEqual(res['8001'], 1000)

    @patch('shadow.Shadow.get_short_link_text', return_value='shortlink')
    def test_get_add_email_text(self, mock_short):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.can_email', return_value=True):
                txt, html, manuals, subject = self.shadow_service.get_add_email_text(
                    'user1', '1.1.1.1', 'en')
        self.assertIn('shortlink', txt)

    def test_get_removal_email_text(self):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.can_email', return_value=True):
                txt, html = self.shadow_service.get_removal_email_text('user1', '1.1.1.1', 'en')
        self.assertIn('revoked', txt[0])

    @patch('shadow.Shadow.start_server')
    @patch('shadow.Shadow.db_query')
    @patch('shadow.Device')
    def test_recover_missing_servers_starts_if_not_running(self, mock_device, mock_db_query, mock_start):
        mock_db_query.return_value = [{'server_port': 8001}]
        mock_device.return_value.is_process_running_pid.return_value = False
        with patch('builtins.open', side_effect=IOError):
            self.shadow_service.recover_missing_servers()
        mock_start.assert_called_once()

    @patch('shadow.Shadow.corrupted_files', return_value=False)
    @patch('shadow.os.path.isfile', return_value=True)
    @patch('shadow.shutil.copyfile')
    def test_backup_success(self, mock_copy, mock_isfile, mock_corrupted):
        self.shadow_service.backup()
        mock_copy.assert_called()

    def test_get_config_settings(self):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.get_start_port', return_value=8000):
                settings = self.shadow_service.get_config_settings()
                self.assertEqual(settings['settings']['port'], 8000)

    def test_configure(self):
        self.shadow_service.service_config = MagicMock()
        with patch('shadow.Shadow.set_enabled') as mock_enabled:
            self.shadow_service.configure({"enabled": True})
            mock_enabled.assert_called_with(True)

    @patch('shadow.Shadow.start_all')
    def test_start_calls_start_all(self, mock_start):
        self.shadow_service.start()
        mock_start.assert_called_once()

    @patch('shadow.Shadow.stop_all')
    def test_stop_calls_stop_all(self, mock_stop):
        self.shadow_service.stop()
        mock_stop.assert_called_once()

    def test_create_link_and_hash_with_prefix(self):
        with patch('shadow.Shadow.is_prefix_enabled', return_value=True):
            with patch('shadow.Shadow.get_prefix', return_value='pre'):
                uri, h = self.shadow_service.create_link_and_hash('pw', '1.1.1.1', 80, 'user')
                self.assertIn('prefix=pre', uri)

    def test_get_prefix_default(self):
        with patch('shadow.Shadow.get_overlayable_config_value', return_value='def'):
            self.assertEqual(self.shadow_service.get_prefix(), 'def')

    def test_is_prefix_enabled(self):
        with patch('shadow.Shadow.get_overlayable_config_value', return_value=True):
            self.assertTrue(self.shadow_service.is_prefix_enabled())

    @patch('shadow.Shadow.clear')
    def test_cleanup_calls_clear(self, mock_clear):
        self.shadow_service.cleanup()
        mock_clear.assert_called_once()

    @patch('shadow.os.path.isfile', return_value=True)
    def test_clear_handles_exception(self, mock_isfile):
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.shutdown.side_effect = Exception("error")
        self.shadow_service.clear()
        self.mock_logger.exception.assert_called()

    def test_usage_summary_calls_deltas(self):
        with patch('shadow.Shadow.get_usage_status_and_deltas', return_value=({'user1': 1}, {'user1': 100})):
            self.assertEqual(self.shadow_service.get_usage_status_summary(), {'user1': 1})
            self.assertEqual(self.shadow_service.get_usage_deltas(), {'user1': 100})

    @patch('shadow.Shadow.db_query')
    @patch('shadow.Shadow.is_enabled', return_value=True)
    def test_get_usage_status_and_deltas_wrap_around(self, mock_enabled, mock_db_query):
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()
        mock_db_query.side_effect = [
            [{'certname': 'user1', 'server_port': 8001}],
            mock_servers_table,
            mock_daily_table
        ]
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 500}'
        mock_servers_table.find_one.return_value = {'certname': 'user1', 'usage': 1000}
        mock_daily_table.find_one.return_value = {
            'certname': 'user1', 'end_usage': 1000, 'start_usage': 900}
        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_status_and_deltas()
        self.assertEqual(deltas['user1'], 500)

    @patch('shadow.Shadow.db_query')
    def test_db_changed_true(self, mock_db_query):
        with patch('shadow.sqli.connect') as mock_sqli:
            mock_conn = mock_sqli.return_value
            mock_conn.execute.return_value.fetchall.return_value = [('user1',)]
            self.assertTrue(self.shadow_service.db_changed())

    @patch('shadow.Shadow.corrupted_files', return_value=False)
    @patch('shadow.Shadow.db_query')
    @patch('shadow.requests')
    @patch('shadow.time.sleep')
    def test_self_test_success_loop(self, mock_sleep, mock_requests, mock_db_query, mock_corrupted):
        mock_db_query.return_value = [{'certname': 'user1', 'server_port': 8001, 'password': 'pw'}]
        mock_requests.get.return_value.status_code = 200
        # Ensure exceptions used in shadow.py are available on the mock
        mock_requests.exceptions.SSLError = Exception
        mock_requests.exceptions.ReadTimeout = Exception

        with patch('shadow.Device') as mock_device:
            mock_device.return_value.execute_cmd_output.return_value = (b'', b'', 0, MagicMock())
            self.assertTrue(self.shadow_service.self_test())

    def test_shadow_conf_file_save_exception(self):
        with patch('builtins.open', side_effect=Exception("write error")):
            self.shadow_service.shadow_conf_file_save(8001, 'pw')
        self.mock_logger.exception.assert_called_with("cannot add shadow conf file")

    @patch('shadow.Shadow.db_query')
    def test_add_user_existing_user(self, mock_db_query):
        mock_table = MagicMock()
        mock_db_query.side_effect = [
            mock_table,
            mock_table,
            []
        ]
        mock_table.find_one.return_value = {
            'certname': 'user1', 'server_port': 8001, 'password': 'pw'}
        with patch('shadow.Shadow.start_server'):
            is_new = self.shadow_service.add_user('user1', '1.1.1.1', 'p', 0, 'en')
        self.assertFalse(is_new)

    @patch('shadow.dataset.connect')
    def test_db_query_returns_db_if_no_args(self, mock_connect):
        mock_connect.return_value = 'db'
        self.assertEqual(self.shadow_service.db_query(), 'db')

    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_new_user(self, mock_db_query):
        """Test case where user is not in usage DB yet."""
        # Setup mocks
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()

        # Mocks for db_query sequence:
        # 1. servers (list of servers)
        # 2. servers (table to find usage)
        # 3. daily (table for daily stats)
        server_info = {'certname': 'user1', 'server_port': 8001}
        mock_db_query.side_effect = [
            [server_info],      # 1. servers list
            mock_servers_table,  # 2. usage_servers_table
            mock_daily_table    # 3. usage_daily_table
        ]

        # Mock socket response with DOUBLE quotes for JSON keys
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 100}'

        # Mock Find in usage table - returns None (New user)
        mock_servers_table.find_one.return_value = None

        # Mock Find in daily table - returns None (New Day/User)
        mock_daily_table.find_one.return_value = None

        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'

            # Execute
            results, deltas = self.shadow_service.get_usage_status_and_deltas()

            # Assertions
            self.assertEqual(deltas['user1'], 100)  # Delta should be full usage

            # Verify upserts
            # Daily upsert for new day
            mock_daily_table.upsert.assert_called()
            call_args = mock_daily_table.upsert.call_args_list[0][0][0]
            self.assertEqual(call_args['start_usage'], 100)
            self.assertEqual(call_args['end_usage'], 100)

            # Usage table upsert
            mock_servers_table.upsert.assert_called()
            call_args_usage = mock_servers_table.upsert.call_args[0][0]
            self.assertEqual(call_args_usage['usage'], 100)

    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_normal_increase(self, mock_db_query):
        """Test case where usage increases normally."""
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()

        server_info = {'certname': 'user1', 'server_port': 8001}
        mock_db_query.side_effect = [
            [server_info],
            mock_servers_table,
            mock_daily_table
        ]

        self.shadow_service.sock = MagicMock()
        # Current usage 200 > Previous 100
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 200}'

        # Previous usage in DB was 100
        mock_servers_table.find_one.return_value = {
            'certname': 'user1', 'usage': 100
        }

        # Daily record exists
        mock_daily_table.find_one.return_value = {
            'certname': 'user1',
            'date': '2026-01-01',
            'start_usage': 50,
            'end_usage': 100
        }

        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'

            results, deltas = self.shadow_service.get_usage_status_and_deltas()

            self.assertEqual(deltas['user1'], 100)  # 200 - 100 = 100 delta

            # Verify upserts
            mock_daily_table.upsert.assert_called()
            call_args = mock_daily_table.upsert.call_args[0][0]

            # usage_value = 200
            # past_delta = 100 - 50 = 50
            # fake_start = 200 - 50 = 150
            self.assertEqual(call_args['start_usage'], 150)
            self.assertEqual(call_args['end_usage'], 200)

    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_wrap_around(self, mock_db_query):
        """Test case where usage wraps around (device reboot)."""
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()

        server_info = {'certname': 'user1', 'server_port': 8001}
        mock_db_query.side_effect = [
            [server_info],
            mock_servers_table,
            mock_daily_table
        ]

        self.shadow_service.sock = MagicMock()
        # Current usage 50 < Previous 1000 (Reboot happened)
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 50}'

        # Previous usage in DB was 1000
        mock_servers_table.find_one.return_value = {
            'certname': 'user1', 'usage': 1000
        }

        # Daily record exists. End usage was 1000.
        mock_daily_table.find_one.return_value = {
            'certname': 'user1',
            'date': '2026-01-01',
            'start_usage': 900,
            'end_usage': 1000
        }

        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'

            results, deltas = self.shadow_service.get_usage_status_and_deltas()

            self.assertEqual(deltas['user1'], 50)

            mock_daily_table.upsert.assert_called()
            call_args = mock_daily_table.upsert.call_args[0][0]
            self.assertEqual(call_args['end_usage'], 50)

    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_socket_error_or_empty(self, mock_db_query):
        """Test case with socket error response."""
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()

        mock_db_query.side_effect = [
            [{'certname': 'user1', 'server_port': 8001}],
            mock_servers_table,
            mock_daily_table
        ]

        self.shadow_service.sock = MagicMock()
        # Empty stats response
        self.shadow_service.sock.recv.return_value = b'stat: {}'

        # Mock that we don't find it in usage DB
        mock_servers_table.find_one.return_value = None

        # Mock daily not found
        mock_daily_table.find_one.return_value = None

        results, deltas = self.shadow_service.get_usage_status_and_deltas()

        self.assertEqual(deltas['user1'], 0)

    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_new_day(self, mock_db_query):
        """Test case where it's a new day (daily record missing)."""
        mock_servers_table = MagicMock()
        mock_daily_table = MagicMock()

        mock_db_query.side_effect = [
            [{'certname': 'user1', 'server_port': 8001}],
            mock_servers_table,
            mock_daily_table
        ]

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'

        # In usage DB
        mock_servers_table.find_one.return_value = {'usage': 100}

        # NOT in daily DB
        mock_daily_table.find_one.return_value = None

        with patch('shadow.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-02'

            results, deltas = self.shadow_service.get_usage_status_and_deltas()

            self.assertEqual(deltas['user1'], 50)  # 150 - 100 = 50

            # Verify new day upsert
            mock_daily_table.upsert.assert_called()
            call_args = mock_daily_table.upsert.call_args[0][0]
            self.assertEqual(call_args['start_usage'], 150)
            self.assertEqual(call_args['end_usage'], 150)
            self.assertEqual(call_args['date'], '2026-01-02')


if __name__ == '__main__':
    unittest.main()
