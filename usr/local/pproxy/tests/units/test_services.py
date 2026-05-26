import pytest
from unittest.mock import MagicMock, patch, mock_open
import json
import os
from services import Services


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_all_services():
    with patch('services.Measurement') as m1, \
            patch('services.Networking') as m2, \
            patch('services.OONI') as m3, \
            patch('services.OpenVPN') as m4, \
            patch('services.Shadow') as m5, \
            patch('services.SSH') as m6, \
            patch('services.Tor') as m7, \
            patch('services.Unbounded') as m8, \
            patch('services.WiFi') as m9, \
            patch('services.Wireguard') as m10, \
            patch('services.WStatus') as m11, \
            patch('services.sanitize') as m12, \
            patch('services.HAProxyService') as m13:
        yield {
            'measurement': m1,
            'networking': m2,
            'ooni': m3,
            'openvpn': m4,
            'shadowsocks': m5,
            'ssh': m6,
            'tor': m7,
            'unbounded': m8,
            'wifi': m9,
            'wireguard': m10,
            'wstatus': m11,
            'sanitize': m12,
            'haproxy': m13,
        }


def test_init(mock_logger, mock_all_services):
    with patch('services.configparser.ConfigParser') as mock_cp:
        s = Services(mock_logger)
        assert len(s.services) == 11
        mock_cp.return_value.read.assert_called_once()
        mock_all_services['wstatus'].assert_called_once()


def test_santizie_service_filename(mock_logger, mock_all_services):
    s = Services(mock_logger)
    mock_all_services['sanitize'].side_effect = lambda x: x
    assert s.santizie_service_filename("My Service!@#") == "myservice"


def test_start_all(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.start_all()
    for service in s.services:
        service['obj'].start.assert_called_once()


def test_stop_all(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.stop_all()
    for service in s.services:
        service['obj'].stop.assert_called_once()


def test_stop_and_start_aliases(mock_logger, mock_all_services):
    s = Services(mock_logger)
    with patch.object(s, 'stop_all') as mock_stop_all:
        s.stop()
        mock_stop_all.assert_called_once()
    with patch.object(s, 'start_all') as mock_start_all:
        s.start()
        mock_start_all.assert_called_once()


def test_restart_all(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.restart_all()
    for service in s.services:
        service['obj'].restart.assert_called_once()


def test_reload_all(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.reload_all()
    for service in s.services:
        service['obj'].reload.assert_called_once()


def test_add_user(mock_logger, mock_all_services):
    s = Services(mock_logger)
    # Test "all"
    for service in s.services:
        service['obj'].add_user.return_value = False
    s.services[0]['obj'].add_user.return_value = True
    assert s.add_user("user", "1.2.3.4", "pass", 1234, "all") is True

    # Test specific service
    target_service = s.services[1]['name']
    s.add_user("user", "1.2.3.4", "pass", 1234, target_service)
    s.services[1]['obj'].add_user.assert_called()


def test_delete_user(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.delete_user("user", "all")
    for service in s.services:
        service['obj'].delete_user.assert_called_with("user")


def test_get_short_link_text(mock_logger, mock_all_services):
    s = Services(mock_logger)
    for service in s.services:
        service['obj'].get_short_link_text.return_value = service['name'] + "_link"

    # All
    links = s.get_short_link_text("user", "1.2.3.4", "all")
    assert "measurement_link" in links
    assert "networking_link" in links

    # Specific
    res = s.get_short_link_text("user", "1.2.3.4", "measurement")
    assert res == "measurement_link"


def test_get_add_email_text(mock_logger, mock_all_services):
    s = Services(mock_logger)
    for service in s.services:
        service['obj'].get_add_email_text.return_value = ("txt", "html", ["att"], "subj")

    txt, html, attachments, subject = s.get_add_email_text("user", "1.2.3.4", "en", "all")
    assert "txt" in txt
    assert "html" in html
    assert len(attachments) == 11
    assert subject == 'Your New VPN Access Details'


def test_get_service_creds_summary(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.services[0]['obj'].is_kindness_mode.return_value = False
    s.services[0]['obj'].get_service_creds_summary.return_value = {"s1": "creds"}
    s.services[1]['obj'].is_kindness_mode.return_value = True

    res = s.get_service_creds_summary("1.2.3.4")
    assert res == {"s1": "creds"}


def test_get_usage_deltas(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.services[0]['obj'].get_usage_deltas.return_value = {"s1": 100}
    res = s.get_usage_deltas(True, True)
    assert res == {"s1": 100}
    s.services[0]['obj'].get_usage_deltas.assert_called_with(True, True)


def test_get_usage_status_summary(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.services[0]['obj'].get_usage_status_summary.return_value = {"s1": "status"}
    res = s.get_usage_status_summary()
    assert res == {"s1": "status"}


def test_get_usage_daily(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.services[0]['obj'].get_usage_daily.return_value = {"server1": {"val": 10}}
    s.services[1]['obj'].get_usage_daily.return_value = {
        "server1": {"other": 20}, "server2": {"val": 30}}

    res = s.get_usage_daily()
    assert res["server1"] == {"val": 10, "other": 20}
    assert res["server2"] == {"val": 30}


def test_get_access_link(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.services[0]['obj'].get_access_link.return_value = None
    s.services[1]['obj'].get_access_link.return_value = "link1"

    assert s.get_access_link("user") == "link1"

    # Test empty
    for service in s.services:
        service['obj'].get_access_link.return_value = None
    assert "empty" in s.get_access_link("user")


def test_recover_missing_servers(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.recover_missing_servers()
    for service in s.services:
        service['obj'].recover_missing_servers.assert_called_once()

    # Test exception
    s.services[0]['obj'].recover_missing_servers.side_effect = Exception("fail")
    s.recover_missing_servers()
    mock_logger.exception.assert_called()


def test_self_test(mock_logger, mock_all_services):
    s = Services(mock_logger)
    for service in s.services:
        service['obj'].self_test.return_value = True
    assert s.self_test() is True

    s.services[0]['obj'].self_test.return_value = False
    assert s.self_test() is False


def test_backup_restore(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.backup_restore()
    for service in s.services:
        service['obj'].backup_restore.assert_called_once()

    # Test exception
    s.services[0]['obj'].backup_restore.side_effect = Exception("fail")
    s.backup_restore()
    mock_logger.exception.assert_called()


def test_server_config_version(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.service_config.has_section.return_value = False
    s.save_server_config_version(5)
    s.service_config.add_section.assert_called_with("server")
    s.service_config.set_field.assert_called_with("server", "version", 5)
    s.service_config.save.assert_called_once()

    s.service_config.has_option.return_value = True
    s.service_config.get_field.return_value = "5"
    assert s.get_saved_server_config_version() == 5

    s.service_config.has_option.return_value = False
    assert s.get_saved_server_config_version() == 1


def test_configure(mock_logger, mock_all_services):
    s = Services(mock_logger)
    config_data = {
        "config_version": 10,
        "services": [
            {"name": "measurement", "settings": {"key": "val"}},
            {"name": "nonexistent", "settings": {}}
        ]
    }

    # Test with dict
    s.configure(config_data)
    s.services[0]['obj'].configure.assert_called_with({"key": "val"})
    mock_logger.error.assert_called_with("nonexistent was not found in services. Not implemented?")

    # Test with JSON string
    mock_logger.reset_mock()
    s.configure(json.dumps(config_data))
    s.services[0]['obj'].configure.assert_called()

    # Test exception in save_server_config_version
    with patch.object(s, 'save_server_config_version', side_effect=Exception("fail")):
        s.configure(config_data)
        mock_logger.exception.assert_called_with("could not save config version")


def test_apply_limits(mock_logger, mock_all_services):
    s = Services(mock_logger)
    s.apply_time_limit()
    for service in s.services:
        service['obj'].apply_time_limit.assert_called_once()

    s.apply_bandwidth_limit()
    for service in s.services:
        service['obj'].apply_bandwidth_limit.assert_called_once()

    s.apply_limits()
    for service in s.services:
        service['obj'].apply_limits.assert_called_once()


def test_get_config_string(mock_logger, mock_all_services):
    s = Services(mock_logger)
    for service in s.services:
        service['obj'].get_config_settings.return_value = {"name": service['name'], "config": {}}

    with patch.object(s, 'get_saved_server_config_version', return_value=123):
        res = s.get_config_string()
        assert res["config_version"] == 123
        assert len(res["services"]) == 11

    res = s.get_config_string(version=456)
    assert res["config_version"] == 456


def test_empty_methods(mock_logger, mock_all_services):
    s = Services(mock_logger)
    assert s.can_email("any") is None
    assert s.is_enanbled("any") is None
