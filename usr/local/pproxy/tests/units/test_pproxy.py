import unittest
from unittest.mock import MagicMock, mock_open, patch

from pproxy import PProxy


def _make_config(overrides=None):
    defaults = {
        ('django', 'url'): 'https://api.test',
        ('django', 'serial_number'): 'SN001',
        ('django', 'id'): 'ID001',
        ('mqtt', 'username'): 'testuser',
        ('mqtt', 'password'): 'testpass',
        ('mqtt', 'host'): 'mqtt.test',
        ('mqtt', 'port'): '8883',
        ('mqtt', 'timeout'): '30',
        ('email', 'enabled'): '0',
        ('email', 'email'): 'from@test',
        ('hw', 'lcd'): '0',
        ('hw', 'buttons'): '0',
        ('hw', 'button-version'): '1',
        ('shadow', 'start-port'): '8388',
        ('openvpn', 'port'): '1194',
    }
    if overrides:
        defaults.update(overrides)
    return defaults


class TestPProxy(unittest.TestCase):

    def setUp(self):
        self.patcher_cp = patch('pproxy.configparser.ConfigParser')
        self.patcher_leds = patch('pproxy.LEDClient')
        self.patcher_wstatus = patch('pproxy.WStatus')
        self.patcher_device = patch('pproxy.Device')
        self.patcher_messages = patch('pproxy.Messages')
        self.patcher_atexit = patch('pproxy.atexit.register')

        self.mock_cp = self.patcher_cp.start()
        self.mock_leds_cls = self.patcher_leds.start()
        self.mock_wstatus_cls = self.patcher_wstatus.start()
        self.mock_device_cls = self.patcher_device.start()
        self.mock_messages_cls = self.patcher_messages.start()
        self.patcher_atexit.start()

        for p in (self.patcher_cp, self.patcher_leds, self.patcher_wstatus,
                  self.patcher_device, self.patcher_messages, self.patcher_atexit):
            self.addCleanup(p.stop)

        cfg_map = _make_config()
        mock_config = self.mock_cp.return_value
        mock_config.get.side_effect = lambda s, o: cfg_map.get((s, o), '')
        mock_config.has_section.return_value = False
        mock_config.has_option.return_value = False
        mock_config.getboolean.return_value = False
        mock_config.getint.return_value = 0

        self.logger = MagicMock()
        self.pp = PProxy(logger=self.logger)

    # --- sanitize_str ---

    def test_sanitize_str_quotes_string_with_spaces(self):
        self.assertEqual(self.pp.sanitize_str('hello world'), "'hello world'")

    def test_sanitize_str_empty_string(self):
        self.assertEqual(self.pp.sanitize_str(''), "''")

    def test_sanitize_str_safe_string_unchanged(self):
        self.assertEqual(self.pp.sanitize_str('safestring'), 'safestring')

    # --- get_tunnel_from_data ---

    def test_get_tunnel_from_data_returns_tunnel_value(self):
        data = {'config': {'tunnel': 'wireguard'}}
        self.assertEqual(self.pp.get_tunnel_from_data(data), 'wireguard')

    def test_get_tunnel_from_data_no_config_key_returns_all(self):
        self.assertEqual(self.pp.get_tunnel_from_data({}), 'all')

    def test_get_tunnel_from_data_config_without_tunnel_returns_all(self):
        self.assertEqual(self.pp.get_tunnel_from_data({'config': {}}), 'all')

    # --- get_server_public_address ---

    def test_get_server_public_address_returns_ip_when_no_ddns(self):
        with patch('pproxy.ipw') as mock_ipw:
            mock_ipw.myip.return_value = '5.6.7.8'
            self.pp.config.has_section.return_value = False
            result = self.pp.get_server_public_address()
        self.assertIn('5.6.7.8', result)

    def test_get_server_public_address_returns_ddns_hostname(self):
        with patch('pproxy.ipw') as mock_ipw:
            mock_ipw.myip.return_value = '5.6.7.8'
            self.pp.config.has_section.return_value = True
            self.pp.config.getboolean.return_value = True
            self.pp.config.get.side_effect = lambda s, o: (
                'my.ddns.host' if (s, o) == ('dyndns', 'hostname') else ''
            )
            result = self.pp.get_server_public_address()
        self.assertEqual(result, 'my.ddns.host')

    # --- get_vpn_file ---

    def test_get_vpn_file_valid_username_returns_path(self):
        result = self.pp.get_vpn_file('user1')
        self.assertEqual(result, '/var/local/pproxy/user1.ovpn')

    def test_get_vpn_file_directory_traversal_blocked(self):
        self.assertIsNone(self.pp.get_vpn_file('../etc/passwd'))

    def test_get_vpn_file_nested_traversal_blocked(self):
        self.assertIsNone(self.pp.get_vpn_file('../../etc/passwd'))

    # --- save_state ---

    def test_save_state_updates_status_file(self):
        self.pp.mqtt_connected = 1
        self.pp.mqtt_reason = 0
        with patch('pproxy.HeartBeat'):
            self.pp.save_state('2')
        self.pp.status.set.assert_any_call('state', '2')
        self.pp.status.set.assert_any_call('mqtt', 1)
        self.pp.status.set.assert_any_call('mqtt-reason', 0)
        self.pp.status.save.assert_called()

    def test_save_state_sends_heartbeat_by_default(self):
        with patch('pproxy.HeartBeat') as mock_hb_cls:
            self.pp.save_state('2')
        mock_hb_cls.return_value.send_heartbeat.assert_called()

    def test_save_state_skips_heartbeat_when_hb_send_false(self):
        with patch('pproxy.HeartBeat') as mock_hb_cls:
            self.pp.save_state('1', hb_send=False)
        mock_hb_cls.assert_not_called()

    # --- process_key ---

    def test_process_key_1_stops_services_when_running(self):
        self.pp.status.get.return_value = '2'
        with patch('pproxy.Services') as mock_svc, patch('pproxy.HeartBeat'):
            self.pp.process_key('1')
        mock_svc.return_value.stop.assert_called_once()

    def test_process_key_1_starts_services_when_stopped(self):
        self.pp.status.get.return_value = '1'
        with patch('pproxy.Services') as mock_svc, patch('pproxy.HeartBeat'):
            self.pp.process_key('1')
        mock_svc.return_value.start.assert_called_once()

    def test_process_key_2_runs_diagnostics(self):
        self.pp.lcd = MagicMock()
        with patch('pproxy.WPDiag') as mock_diag, \
             patch('pproxy.HeartBeat'), \
             patch('pproxy.time.sleep'):
            self.pp.process_key('2')
        mock_diag.return_value.get_error_code.assert_called_once()

    def test_process_key_3_stops_and_powers_off(self):
        self.pp.lcd = MagicMock()
        with patch('pproxy.Services') as mock_svc, \
             patch('pproxy.HeartBeat'), \
             patch('pproxy.time.sleep'):
            self.pp.process_key('3')
        mock_svc.return_value.stop.assert_called_once()
        self.pp.device.turn_off.assert_called_once()

    def test_process_key_unknown_is_noop(self):
        with patch('pproxy.Services') as mock_svc:
            self.pp.process_key('9')
        mock_svc.return_value.start.assert_not_called()
        mock_svc.return_value.stop.assert_not_called()

    # --- on_channel_state ---

    def test_on_channel_state_connected_sets_attrs_and_saves_state(self):
        with patch('pproxy.Thread') as mock_thread:
            self.pp.on_channel_state('mqtt', 1, 0)
        self.assertEqual(self.pp.mqtt_connected, 1)
        self.assertEqual(self.pp.mqtt_reason, 0)
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

    def test_on_channel_state_disconnected_pulses_leds(self):
        self.pp.on_channel_state('mqtt', 0, 5)
        self.assertEqual(self.pp.mqtt_connected, 0)
        self.assertEqual(self.pp.mqtt_reason, 5)
        self.pp.leds.pulse.assert_called_once()

    # --- on_channel_message ---

    def test_on_channel_message_enqueues_valid_payload(self):
        ack = self.pp.on_channel_message('mqtt', {'action': 'reboot_device'})
        self.assertEqual(ack, {'status': 'queued'})
        source, payload = self.pp.queue.get_nowait()
        self.assertEqual(source, 'mqtt')
        self.assertEqual(payload['action'], 'reboot_device')

    def test_on_channel_message_routes_slow_action_to_slow_lane(self):
        ack = self.pp.on_channel_message('poller',
                                         {'action': 'add_user', 'cert_name': 'u1'})
        self.assertEqual(ack, {'status': 'queued'})
        self.assertTrue(self.pp.queue.empty())
        source, payload = self.pp.slow_queue.get_nowait()
        self.assertEqual(payload['action'], 'add_user')

    def test_on_channel_message_rejects_when_fast_queue_full(self):
        while True:
            try:
                self.pp.queue.put_nowait(('x', {'action': 'reboot_device'}))
            except Exception:
                break
        ack = self.pp.on_channel_message('mqtt', {'action': 'reboot_device'})
        self.assertEqual(ack, {'status': 'busy'})

    def test_on_channel_message_rejects_when_slow_queue_full(self):
        while True:
            try:
                self.pp.slow_queue.put_nowait(('x', {'action': 'add_user'}))
            except Exception:
                break
        ack = self.pp.on_channel_message('poller',
                                         {'action': 'add_user', 'cert_name': 'u'})
        self.assertEqual(ack, {'status': 'busy'})

    def test_on_channel_message_notification_triggers_fetch_not_enqueued(self):
        with patch('pproxy.Thread') as mock_thread:
            ack = self.pp.on_channel_message(
                'mqtt', {'action': 'notification', 'message_id': 42})
        self.assertEqual(ack, {'status': 'queued'})
        self.assertTrue(self.pp.queue.empty())
        mock_thread.assert_called_once_with(target=self.pp.trigger_fetch)
        mock_thread.return_value.start.assert_called_once()

    def test_on_channel_message_invalid_payload_discarded(self):
        ack = self.pp.on_channel_message('mqtt', {'not_an_action': 1})
        self.assertEqual(ack, {'status': 'discarded'})
        self.assertTrue(self.pp.queue.empty())

    def test_on_channel_message_invalid_payload_content_not_logged(self):
        # regression guard: payload content must not reach the (prod-written) log
        self.pp.on_channel_message('mqtt', {'secret': 'SENSITIVE-VALUE'})
        logged = []
        for _, args, _ in self.logger.method_calls:
            logged.extend(str(a) for a in args)
        self.assertNotIn('SENSITIVE-VALUE', " ".join(logged))

    def test_on_channel_message_non_dict_payload_discarded(self):
        ack = self.pp.on_channel_message('mqtt', "just a string")
        self.assertEqual(ack, {'status': 'discarded'})
        self.assertTrue(self.pp.queue.empty())

    def test_trigger_fetch_sends_cmd_to_poller(self):
        self.pp.channel = MagicMock()
        self.pp.trigger_fetch()
        self.pp.channel.send_cmd.assert_called_once_with(
            'poller', {'cmd': 'fetch-now'})

    def test_trigger_fetch_survives_channel_error(self):
        self.pp.channel = MagicMock()
        self.pp.channel.send_cmd.side_effect = Exception('poller gone')
        self.pp.trigger_fetch()  # must not raise
        self.logger.exception.assert_called()

    # --- dispatch_loop ---

    def test_dispatch_loop_feeds_handler_in_order(self):
        calls = []

        def handler(data, lock):
            calls.append(data['action'])
            if data['action'] == 'last':
                raise SystemExit()

        self.pp.queue.put(('mqtt', {'action': 'first'}))
        self.pp.queue.put(('poller', {'action': 'last'}))
        with patch.object(self.pp, 'on_message_handler', side_effect=handler):
            with self.assertRaises(SystemExit):
                self.pp.dispatch_loop()
        self.assertEqual(calls, ['first', 'last'])

    def test_dispatch_loop_continues_after_handler_exception(self):
        calls = []

        def handler(data, lock):
            calls.append(data['action'])
            if data['action'] == 'boom':
                raise ValueError('handler failed')
            raise SystemExit()

        self.pp.queue.put(('mqtt', {'action': 'boom'}))
        self.pp.queue.put(('mqtt', {'action': 'last'}))
        with patch.object(self.pp, 'on_message_handler', side_effect=handler):
            with self.assertRaises(SystemExit):
                self.pp.dispatch_loop()
        self.assertEqual(calls, ['boom', 'last'])

    def test_slow_dispatch_loop_feeds_handler_in_order(self):
        calls = []

        def handler(data, lock):
            calls.append((data['action'], data['cert_name']))
            if data['action'] == 'delete_user':
                raise SystemExit()

        self.pp.slow_queue.put(('mqtt', {'action': 'add_user',
                                         'cert_name': 'u1'}))
        self.pp.slow_queue.put(('mqtt', {'action': 'delete_user',
                                         'cert_name': 'u1'}))
        with patch.object(self.pp, 'on_message_handler', side_effect=handler):
            with self.assertRaises(SystemExit):
                self.pp.slow_dispatch_loop()
        self.assertEqual(calls, [('add_user', 'u1'), ('delete_user', 'u1')])

    def test_slow_dispatch_loop_continues_after_handler_exception(self):
        calls = []

        def handler(data, lock):
            calls.append(data['cert_name'])
            if data['cert_name'] == 'boom':
                raise ValueError('handler failed')
            raise SystemExit()

        self.pp.slow_queue.put(('mqtt', {'action': 'add_user',
                                         'cert_name': 'boom'}))
        self.pp.slow_queue.put(('mqtt', {'action': 'add_user',
                                         'cert_name': 'ok'}))
        with patch.object(self.pp, 'on_message_handler', side_effect=handler):
            with self.assertRaises(SystemExit):
                self.pp.slow_dispatch_loop()
        self.assertEqual(calls, ['boom', 'ok'])

    # --- on_message_handler ---

    def test_on_message_handler_reboot_device(self):
        with patch('pproxy.Services'), patch('pproxy.HeartBeat'):
            self.pp.on_message_handler({'action': 'reboot_device'}, MagicMock())
        self.pp.device.reboot.assert_called_once()

    def test_on_message_handler_start_service(self):
        with patch('pproxy.Services') as mock_svc, patch('pproxy.HeartBeat'):
            self.pp.on_message_handler({'action': 'start_service'}, MagicMock())
        mock_svc.return_value.start_all.assert_called_once()

    def test_on_message_handler_stop_service(self):
        with patch('pproxy.Services') as mock_svc, patch('pproxy.HeartBeat'):
            self.pp.on_message_handler({'action': 'stop_service'}, MagicMock())
        mock_svc.return_value.stop_all.assert_called_once()

    def test_on_message_handler_restart_service(self):
        with patch('pproxy.Services') as mock_svc:
            self.pp.on_message_handler({'action': 'restart_service'}, MagicMock())
        mock_svc.return_value.restart_all.assert_called_once()

    def test_on_message_handler_reload_service(self):
        with patch('pproxy.Services') as mock_svc:
            self.pp.on_message_handler({'action': 'reload_service'}, MagicMock())
        mock_svc.return_value.reload_all.assert_called_once()

    def test_on_message_handler_delete_user_calls_service(self):
        data = {'action': 'delete_user', 'cert_name': 'user1'}
        with patch('pproxy.Services') as mock_svc, \
             patch.object(self.pp, 'get_server_public_address', return_value='1.2.3.4'):
            self.pp.on_message_handler(data, MagicMock())
        mock_svc.return_value.delete_user.assert_called_once()

    def test_on_message_handler_wipe_reboots(self):
        with patch('pproxy.HeartBeat'):
            self.pp.on_message_handler({'action': 'wipe_device'}, MagicMock())
        self.pp.device.reboot.assert_called_once()

    def test_on_message_handler_update_pproxy(self):
        with patch('pproxy.Services'):
            self.pp.on_message_handler({'action': 'update-pproxy'}, MagicMock())
        self.pp.device.update.assert_called_once()

    # --- fetch_config ---

    def test_fetch_config_passes_config_to_services(self):
        mock_services = MagicMock()
        self.pp.device.get_device_config_backend.return_value = {'config': {'k': 'v'}}
        self.pp.fetch_config(mock_services)
        mock_services.configure.assert_called_once_with({'k': 'v'})

    def test_fetch_config_handles_exception_gracefully(self):
        mock_services = MagicMock()
        self.pp.device.get_device_config_backend.side_effect = Exception('fail')
        self.pp.fetch_config(mock_services)  # must not raise
        mock_services.configure.assert_not_called()

    # --- send_mail ---

    def test_send_mail_skips_when_email_disabled(self):
        self.pp.config.get = MagicMock(return_value='0')
        with patch('pproxy.smtplib.SMTP') as mock_smtp:
            self.pp.send_mail('from@t', 'to@t', 'sub', 'body', '', [])
        mock_smtp.assert_not_called()

    # --- on_message_handler add_user ---

    def test_on_message_handler_add_user_calls_service(self):
        data = {'action': 'add_user', 'cert_name': 'u1', 'language': 'en'}
        mock_lock = MagicMock()
        with patch('pproxy.Services') as mock_svc, \
             patch.object(self.pp, 'get_server_public_address', return_value='1.2.3.4'):
            self.pp.on_message_handler(data, mock_lock)
        mock_svc.return_value.add_user.assert_called_once()
        mock_lock.acquire.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_on_message_handler_add_user_exception_releases_lock(self):
        data = {'action': 'add_user', 'cert_name': 'u1', 'language': 'en'}
        mock_lock = MagicMock()
        with patch('pproxy.Services') as mock_svc, \
             patch.object(self.pp, 'get_server_public_address', return_value='1.2.3.4'):
            mock_svc.return_value.add_user.side_effect = Exception('add failed')
            self.pp.on_message_handler(data, mock_lock)
        mock_lock.release.assert_called_once()

    # --- on_message_handler set_ddns ---

    def test_on_message_handler_set_ddns_writes_config(self):
        data = {'action': 'set_ddns', 'enabled': '1', 'hostname': 'my.host.test'}
        with patch('pproxy.Services'), patch('builtins.open', mock_open()):
            self.pp.on_message_handler(data, MagicMock())
        self.pp.config.set.assert_any_call('dyndns', 'enabled', '1')
        self.pp.config.set.assert_any_call('dyndns', 'hostname', 'my.host.test')
        self.pp.config.write.assert_called_once()

    def test_on_message_handler_set_ddns_stores_raw_value(self):
        """Regression guard: special-char values must not be shell-quoted."""
        data = {'action': 'set_ddns', 'password': "my'pass"}
        with patch('pproxy.Services'), patch('builtins.open', mock_open()):
            self.pp.on_message_handler(data, MagicMock())
        self.pp.config.set.assert_any_call('dyndns', 'password', "my'pass")

    def test_on_message_handler_set_creds_stores_raw_value(self):
        """Regression guard: credentials must not be shell-quoted before storing."""
        data = {
            'action': 'set_creds',
            'host': 'smtp.example.com',
            'port': '587',
            'username': 'user@example.com',
            'email': 'from@example.com',
            'password': "p@ss'word",
        }
        with patch('pproxy.Services'), patch('builtins.open', mock_open()):
            self.pp.on_message_handler(data, MagicMock())
        self.pp.config.set.assert_any_call('email', 'password', "p@ss'word")
        self.pp.config.set.assert_any_call('email', 'host', 'smtp.example.com')

    # --- send_mail enabled ---

    def _enable_email(self):
        cfg = _make_config({
            ('email', 'enabled'): '1',
            ('email', 'host'): 'smtp.test',
            ('email', 'port'): '587',
            ('email', 'username'): 'u',
            ('email', 'password'): 'p',
        })
        self.pp.config.get.side_effect = lambda s, o: cfg.get((s, o), '')
        self.pp.config.has_option.return_value = False

    def test_send_mail_sends_when_enabled(self):
        self._enable_email()
        with patch('pproxy.smtplib.SMTP') as mock_smtp:
            self.pp.send_mail('from@t', 'to@t', 'sub', 'body text', '', [])
        mock_smtp.return_value.sendmail.assert_called_once()

    def test_send_mail_skips_empty_body(self):
        self._enable_email()
        with patch('pproxy.smtplib.SMTP') as mock_smtp:
            self.pp.send_mail('from@t', 'to@t', 'sub', '', '', [])
        mock_smtp.assert_not_called()

    def test_send_mail_logs_smtp_exception(self):
        self._enable_email()
        with patch('pproxy.smtplib.SMTP') as mock_smtp:
            mock_smtp.return_value.sendmail.side_effect = Exception('smtp fail')
            self.pp.send_mail('from@t', 'to@t', 'sub', 'body', '', [])
        self.logger.error.assert_called()

    # --- cleanup ---

    def test_cleanup_blanks_leds(self):
        self.pp.cleanup()
        self.pp.leds.blank.assert_called_once()


if __name__ == '__main__':
    unittest.main()
