import unittest
from unittest.mock import MagicMock, patch, mock_open
import os
import sys

up_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)) + '/../../')
if up_dir not in sys.path:
    sys.path.append(up_dir)

import wireguard  # noqa: E402


class TestWireguardUsage(unittest.TestCase):

    def setUp(self):
        self.mock_logger = MagicMock()
        with patch('wireguard.Service.__init__'), patch('wireguard.Usage'):
            self.wg = wireguard.Wireguard.__new__(wireguard.Wireguard)
            self.wg.logger = self.mock_logger
            self.wg.config = MagicMock()
            self.wg.config.get.side_effect = lambda s, k: {
                ('usage', 'db-path'): '/tmp/test_wg_usage.db',
                ('wireguard', 'enabled'): '1',
            }.get((s, k))
            self.wg.usage = MagicMock()
            self.wg.name = "wireguard"

    # --- get_public_key_for_user ---

    @patch('wireguard.Wireguard.santizie_service_filename', return_value='alice')
    def test_get_public_key_for_user_reads_file(self, mock_sanitize):
        m = mock_open(read_data='PUBKEY123\n')
        with patch('builtins.open', m):
            result = self.wg.get_public_key_for_user('alice')
        self.assertEqual(result, 'PUBKEY123')

    @patch('wireguard.Wireguard.santizie_service_filename', return_value='alice')
    def test_get_public_key_for_user_missing_file(self, mock_sanitize):
        with patch('builtins.open', side_effect=FileNotFoundError):
            result = self.wg.get_public_key_for_user('alice')
        self.assertIsNone(result)
        self.mock_logger.exception.assert_called()

    # --- get_current_reading ---

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice', 'bob'])
    @patch('wireguard.Wireguard.get_public_key_for_user',
           side_effect=lambda c: {'alice': 'PUBKEY_A', 'bob': 'PUBKEY_B'}.get(c))
    @patch('wireguard.subprocess.run')
    def test_get_current_reading_sums_rx_and_tx(self, mock_run, mock_pubkey, mock_users):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b'PUBKEY_A\t100\t200\nPUBKEY_B\t50\t50\n'
        result = self.wg.get_current_reading()
        self.assertEqual(result['alice'], 300)
        self.assertEqual(result['bob'], 100)

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_public_key_for_user', return_value='PUBKEY_A')
    @patch('wireguard.subprocess.run')
    def test_get_current_reading_wg_failure_returns_empty(self, mock_run, mock_pubkey, mock_users):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b'Cannot find device wg0'
        result = self.wg.get_current_reading()
        self.assertEqual(result, {})

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_public_key_for_user', return_value='PUBKEY_A')
    @patch('wireguard.subprocess.run')
    def test_get_current_reading_unknown_peer_ignored(self, mock_run, mock_pubkey, mock_users):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b'UNKNOWN_KEY\t100\t200\n'
        result = self.wg.get_current_reading()
        self.assertEqual(result, {})

    @patch('wireguard.Wireguard.get_users_list', return_value=[])
    def test_get_current_reading_no_users_returns_empty(self, mock_users):
        result = self.wg.get_current_reading()
        self.assertEqual(result, {})

    # --- get_usage_for_servers ---

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_current_reading', return_value={'alice': 500})
    @patch('wireguard.Wireguard.is_enabled', return_value=True)
    def test_get_usage_for_servers_active_user(self, mock_enabled, mock_reading, mock_users):
        self.wg.usage.update_recorded_usage.return_value = (500, 500, 500)
        statuses, deltas = self.wg.get_usage_for_servers()
        self.assertEqual(statuses['alice'], 1)
        self.assertEqual(deltas['alice'], 4000)  # 500 * 8

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_current_reading', return_value={'alice': 0})
    @patch('wireguard.Wireguard.is_enabled', return_value=True)
    def test_get_usage_for_servers_zero_delta_not_connected(self, mock_enabled, mock_reading, mock_users):
        # delta == 0 must not count as connected
        self.wg.usage.update_recorded_usage.return_value = (0, 0, 0)
        statuses, deltas = self.wg.get_usage_for_servers()
        self.assertEqual(statuses['alice'], 0)

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_current_reading', return_value={})
    @patch('wireguard.Wireguard.is_enabled', return_value=True)
    def test_get_usage_for_servers_missing_peer(self, mock_enabled, mock_reading, mock_users):
        statuses, deltas = self.wg.get_usage_for_servers()
        self.assertEqual(statuses['alice'], -1)
        self.assertEqual(deltas['alice'], -1)

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.get_current_reading', return_value={'alice': 1000})
    @patch('wireguard.Wireguard.is_enabled', return_value=True)
    def test_get_usage_for_servers_periodic_uses_long_term(self, mock_enabled, mock_reading, mock_users):
        self.wg.usage.update_recorded_usage.return_value = (200, 1500, 200)
        statuses, deltas = self.wg.get_usage_for_servers(periodic=True, clear_counters=True)
        self.assertEqual(deltas['alice'], 12000)  # 1500 * 8
        call_kwargs = self.wg.usage.update_recorded_usage.call_args[1]
        self.assertTrue(call_kwargs['clear_long_term'])
        self.assertFalse(call_kwargs['clear_short_term'])

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    @patch('wireguard.Wireguard.is_enabled', return_value=False)
    def test_get_usage_for_servers_disabled_returns_empty(self, mock_enabled, mock_users):
        statuses, deltas = self.wg.get_usage_for_servers()
        self.assertEqual(statuses, {})
        self.assertEqual(deltas, {})

    # --- get_usage_status_summary ---

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    def test_get_usage_status_summary_connected(self, mock_users):
        self.wg.usage.get_record_for_cert.return_value = {'short_term': 100}
        result = self.wg.get_usage_status_summary()
        self.assertEqual(result['alice'], 1)

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    def test_get_usage_status_summary_not_connected(self, mock_users):
        self.wg.usage.get_record_for_cert.return_value = {'short_term': 0}
        result = self.wg.get_usage_status_summary()
        self.assertEqual(result['alice'], 0)

    @patch('wireguard.Wireguard.get_users_list', return_value=['alice'])
    def test_get_usage_status_summary_never_connected(self, mock_users):
        # No DB record: get_record_for_cert returns None, None['short_term'] raises → -1
        self.wg.usage.get_record_for_cert.return_value = None
        result = self.wg.get_usage_status_summary()
        self.assertEqual(result['alice'], -1)

    # --- get_usage_deltas ---

    @patch('wireguard.Wireguard.get_usage_for_servers',
           return_value=({'alice': 1}, {'alice': 800}))
    def test_get_usage_deltas_delegates_to_get_usage_for_servers(self, mock_servers):
        result = self.wg.get_usage_deltas(long_term=True, clear_counters=True)
        mock_servers.assert_called_once_with(periodic=True, clear_counters=True)
        self.assertEqual(result, {'alice': 800})

    # --- del_user_usage / delete_user ---

    def test_del_user_usage_delegates_to_usage(self):
        self.wg.del_user_usage('alice')
        self.wg.usage.del_user_usage.assert_called_once_with('alice')

    @patch('wireguard.Wireguard.santizie_service_filename', return_value='alice')
    @patch('wireguard.Wireguard.execute_cmd')
    def test_delete_user_cleans_up_usage(self, mock_exec, mock_sanitize):
        self.wg.delete_user('alice')
        self.wg.usage.del_user_usage.assert_called_once_with('alice')


class TestWireguardUserRegistration(unittest.TestCase):

    def setUp(self):
        self.mock_logger = MagicMock()
        with patch('wireguard.Service.__init__'), patch('wireguard.Usage'):
            self.wg = wireguard.Wireguard.__new__(wireguard.Wireguard)
            self.wg.logger = self.mock_logger
            self.wg.config = MagicMock()
            self.wg.usage = MagicMock()
            self.wg.name = "wireguard"

    # --- is_user_registered ---

    @patch('wireguard.Wireguard.santizie_service_filename', return_value='alice')
    @patch('wireguard.os.path.exists', return_value=True)
    def test_is_user_registered_true(self, mock_exists, mock_sanitize):
        self.assertTrue(self.wg.is_user_registered('alice'))

    @patch('wireguard.Wireguard.santizie_service_filename', return_value='alice')
    @patch('wireguard.os.path.exists', return_value=False)
    def test_is_user_registered_false(self, mock_exists, mock_sanitize):
        self.assertFalse(self.wg.is_user_registered('alice'))

    # --- get_external_ip_port_in_conf ---

    @patch('wireguard.Wireguard.get_user_config_file_path',
           return_value='/var/local/pproxy/users/alice/wg.conf')
    def test_get_external_ip_port_valid_endpoint(self, mock_path):
        conf = '[Peer]\nEndpoint = 1.2.3.4:51820\nPublicKey = abc\n'
        with patch('builtins.open', mock_open(read_data=conf)):
            ip, port = self.wg.get_external_ip_port_in_conf('alice')
        self.assertEqual(ip, '1.2.3.4')
        self.assertEqual(port, 51820)

    @patch('wireguard.Wireguard.get_user_config_file_path',
           return_value='/var/local/pproxy/users/alice/wg.conf')
    def test_get_external_ip_port_malformed_endpoint(self, mock_path):
        conf = '[Peer]\nEndpoint = badvalue\nPublicKey = abc\n'
        with patch('builtins.open', mock_open(read_data=conf)):
            ip, port = self.wg.get_external_ip_port_in_conf('alice')
        self.assertIsNone(ip)
        self.assertIsNone(port)

    @patch('wireguard.Wireguard.get_user_config_file_path', return_value=None)
    def test_get_external_ip_port_user_not_registered(self, mock_path):
        ip, port = self.wg.get_external_ip_port_in_conf('unknown')
        self.assertIsNone(ip)
        self.assertIsNone(port)


if __name__ == '__main__':
    unittest.main()
