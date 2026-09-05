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
        self.assertEqual(mock_db_query.call_count, 1)
        mock_table.delete.assert_called_once_with(certname='user1')
        mock_table.db.close.assert_called_once()

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

    def test_get_current_reading_success(self):
        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 1000}'
        res = self.shadow_service.get_current_reading()
        self.assertEqual(res['8001'], 1000)

    @patch('shadow.Shadow.get_short_link_text', return_value='shortlink')
    def test_get_add_email_text(self, mock_short):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.can_email', return_value=True):
                txt, html, manuals, subject = self.shadow_service.get_add_email_text(
                    'user1', '1.1.1.1', 'en')
        self.assertIn('shortlink', txt)

    @patch('shadow.Shadow.get_short_link_text', return_value='shortlink')
    def test_get_add_email_text_new_user_gets_welcome_text(self, mock_short):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.can_email', return_value=True):
                txt, html, manuals, subject = self.shadow_service.get_add_email_text(
                    'user1', '1.1.1.1', 'en', is_new_user=True)
        self.assertIn('You have been granted access', txt)
        self.assertIn('You have been granted access', html)
        self.assertEqual(subject, "Your New VPN Access Details")

    @patch('shadow.Shadow.get_short_link_text', return_value='shortlink')
    def test_get_add_email_text_existing_user_gets_update_text(self, mock_short):
        with patch('shadow.Shadow.is_enabled', return_value=True):
            with patch('shadow.Shadow.can_email', return_value=True):
                txt, html, manuals, subject = self.shadow_service.get_add_email_text(
                    'user1', '1.1.1.1', 'en', is_new_user=False)
        self.assertIn('access link to the private VPN server is updated', txt)
        self.assertIn('access link to the private VPN server is updated', html)
        self.assertEqual(subject, "Update to  Your VPN Access Details")

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

    def test_create_link_and_hash_strips_non_printable_certname(self):
        uri, h = self.shadow_service.create_link_and_hash('pw', '1.1.1.1', 80, 'user​\n')
        self.assertTrue(uri.endswith('#WEPN-user'))

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

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_usage_summary_calls_deltas(self, mock_shadow_db_query, mock_usage_db_query):
        mock_usage_table = MagicMock()
        mock_usage_table.find_one.return_value = {'certname': 'user1', 'short_term': 100}
        mock_usage_db_query.return_value = mock_usage_table

        mock_shadow_table = MagicMock()
        mock_shadow_table.__iter__.return_value = [{'certname': 'user1'}]
        mock_shadow_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_shadow_table

        self.assertEqual(self.shadow_service.get_usage_status_summary(), {'user1': 1})

        with patch('usage.Usage.update_recorded_usage') as mock_update, \
                patch('shadow.Shadow.is_enabled', return_value=True):
            mock_update.return_value = (100, 200, 50)
            self.shadow_service.sock = MagicMock()
            self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'
            results, deltas = self.shadow_service.get_usage_for_servers()
            self.assertEqual(deltas, {'user1': 800})  # 50 * 8 = 400

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

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    @patch('shadow.Shadow.is_enabled', return_value=True)
    def test_get_usage_status_and_deltas_success(self, mock_enabled, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = None

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 1000}'

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_for_servers()
        self.assertEqual(results['user1'], 1)
        self.assertEqual(deltas['user1'], 8000)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_wrap_around(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = {
            'certname': 'user1', 'raw_usage': 1000, 'short_term': 0, 'long_term': 0}

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat:{"8001": 500}'

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_for_servers()
        self.assertEqual(deltas['user1'], 4000)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_new_user(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = None

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 100}'

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_for_servers()
            self.assertEqual(deltas['user1'], 800)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_normal_increase(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = {
            'certname': 'user1', 'raw_usage': 100, 'short_term': 0, 'long_term': 0}

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 200}'

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            results, deltas = self.shadow_service.get_usage_for_servers()
            self.assertEqual(deltas['user1'], 800)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_and_deltas_socket_error_or_empty(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = None

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {}'
        mock_servers_table.find_one.return_value = None

        results, deltas = self.shadow_service.get_usage_for_servers()
        self.assertEqual(deltas['user1'], 0)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_get_usage_status_new_day(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = {'raw_usage': 100, 'short_term': 0, 'long_term': 0}

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'
        mock_servers_table.find_one.return_value = {'usage': 100}

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-02'
            results, deltas = self.shadow_service.get_usage_for_servers()
            self.assertEqual(deltas['user1'], 400)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_daily_logic_fix_normal_increase(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_table = MagicMock()
        mock_usage_db_query.return_value = mock_usage_table
        mock_usage_table.find_one.return_value = {'raw_usage': 100, 'short_term': 0, 'long_term': 0}

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 200}'
        mock_servers_table.find_one.return_value = {'usage': 100}

        with patch('usage.datetime') as mock_date:
            mock_date.today.return_value.strftime.return_value = '2026-01-01'
            self.shadow_service.get_usage_for_servers()

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_periodic_measurement_accumulation(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_usage_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_db_query.return_value = mock_usage_table

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'
        mock_usage_table.find_one.side_effect = [
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_delta_for_cert
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_record_for_cert
        ]

        results, deltas = self.shadow_service.get_usage_for_servers(periodic=True)

        mock_usage_table.upsert.assert_called()
        call_args = mock_usage_table.upsert.call_args[0][0]
        self.assertEqual(call_args['long_term'], 1050)
        self.assertEqual(deltas['user1'], 8400)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_periodic_measurement_recording(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_usage_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_db_query.return_value = mock_usage_table

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'

        mock_usage_table.find_one.side_effect = [
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_delta_for_cert
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_record_for_cert
        ]

        self.shadow_service.get_usage_for_servers(periodic=True)

        mock_usage_table.upsert.assert_called()
        update_args = mock_usage_table.upsert.call_args[0][0]
        self.assertEqual(update_args['long_term'], 1050)

    @patch('usage.Usage.db_query')
    @patch('shadow.Shadow.db_query')
    def test_periodic_measurement_reporting_flag(self, mock_shadow_db_query, mock_usage_db_query):
        mock_servers_table = MagicMock()
        mock_usage_table = MagicMock()
        mock_servers_table.all.return_value = [{'certname': 'user1', 'server_port': 8001}]
        mock_shadow_db_query.return_value = mock_servers_table

        mock_usage_db_query.return_value = mock_usage_table

        self.shadow_service.sock = MagicMock()
        self.shadow_service.sock.recv.return_value = b'stat: {"8001": 150}'
        mock_usage_table.find_one.side_effect = [
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_delta_for_cert
            {'raw_usage': 100, 'short_term': 0, 'long_term': 1000},  # get_record_for_cert
        ]

        results, deltas = self.shadow_service.get_usage_for_servers(periodic=True)
        self.assertEqual(deltas['user1'], 8400)


if __name__ == '__main__':
    unittest.main()
