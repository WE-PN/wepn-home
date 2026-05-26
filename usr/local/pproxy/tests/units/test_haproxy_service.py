import sys
import os
import configparser
import socket
import tempfile
import pytest
from unittest.mock import MagicMock, patch, mock_open

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

for m in [
    'qrcode', 'Adafruit_SSD1306', 'getmac', 'pystemd', 'pystemd.systemd1',
    'distro', 'netifaces', 'psutil', 'upnpclient', 'packaging',
    'packaging.version', 'adafruit_rgb_display', 'adafruit_rgb_display.st7789',
    'sanitize_filename', 'RPi', 'RPi.GPIO', 'luma', 'luma.core',
    'luma.core.interface', 'luma.core.interface.serial', 'luma.oled',
    'luma.oled.device', 'board', 'sqlalchemy', 'sqlalchemy.exc', 'dataset',
    'digitalio', 'busio',
]:
    if m not in sys.modules:
        sys.modules[m] = MagicMock()

with patch('logging.config.fileConfig'):
    from haproxy_service import HAProxyService, _HAPROXY_PEM, _HAPROXY_DIR


def _temp_config(content):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False)
    f.write(content)
    f.close()
    return f.name


@pytest.fixture
def svc():
    with patch('haproxy_service.Device'), \
            patch('service.WStatus'), \
            patch('service.Device'), \
            patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'):
        return HAProxyService(MagicMock())


# ── is_enabled ───────────────────────────────────────────────────────────────

def test_is_enabled_false_by_default(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'):
        assert svc.is_enabled() is False


def test_is_enabled_true_when_set(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg):
            assert svc.is_enabled() is True
    finally:
        os.unlink(cfg)


def test_is_enabled_false_when_section_missing(svc):
    cfg = _temp_config("[dyndns]\nenabled = 0\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg):
            assert svc.is_enabled() is False
    finally:
        os.unlink(cfg)


# ── _cert_ready ──────────────────────────────────────────────────────────────

def test_cert_ready_true_when_file_exists(svc):
    with patch('haproxy_service.os.path.exists', return_value=True):
        assert svc._cert_ready() is True


def test_cert_ready_false_when_missing(svc):
    with patch('haproxy_service.os.path.exists', return_value=False):
        assert svc._cert_ready() is False


# ── start ────────────────────────────────────────────────────────────────────

def test_start_does_nothing_when_disabled(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'), \
            patch('haproxy_service.Device') as mock_device:
        svc.start()
    mock_device.return_value.execute_setuid.assert_not_called()


def test_start_does_nothing_when_cert_missing(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg), \
                patch('haproxy_service.os.path.exists', return_value=False), \
                patch('haproxy_service.Device') as mock_device:
            svc.start()
        mock_device.return_value.execute_setuid.assert_not_called()
    finally:
        os.unlink(cfg)


def test_start_calls_setuid_when_ready(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg), \
                patch('haproxy_service.os.path.exists', return_value=True), \
                patch('haproxy_service.Device') as mock_device:
            svc.start()
        mock_device.return_value.execute_setuid.assert_called_once_with("0 7 1")
    finally:
        os.unlink(cfg)


# ── stop ─────────────────────────────────────────────────────────────────────

def test_stop_calls_setuid_when_running(svc):
    with patch.object(svc, 'is_running', return_value=True), \
            patch('haproxy_service.Device') as mock_device:
        svc.stop()
    mock_device.return_value.execute_setuid.assert_called_once_with("0 7 0")


def test_stop_skips_setuid_when_not_running(svc):
    with patch.object(svc, 'is_running', return_value=False), \
            patch('haproxy_service.Device') as mock_device:
        svc.stop()
    mock_device.return_value.execute_setuid.assert_not_called()


# ── get_config_settings ──────────────────────────────────────────────────────

def test_get_config_settings_shape(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'):
        result = svc.get_config_settings()
    assert result["name"] == "haproxy"
    assert "enabled" in result["settings"]


# ── _get_ports ───────────────────────────────────────────────────────────────

def test_get_ports_defaults(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'):
        port_ssl, port_a, port_b, port_signal = svc._get_ports()
    assert port_ssl == 443
    assert port_a == 5222
    assert port_b == 4244
    assert port_signal == 6443


def test_get_ports_from_config(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\nport_ssl = 8443\nport_a = 1234\nport_b = 5678\nport_signal = 7443\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg):
            port_ssl, port_a, port_b, port_signal = svc._get_ports()
        assert port_ssl == 8443
        assert port_a == 1234
        assert port_b == 5678
        assert port_signal == 7443
    finally:
        os.unlink(cfg)


# ── forward_ports ─────────────────────────────────────────────────────────────

def test_forward_ports_skips_when_disabled(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'), \
            patch('haproxy_service.Device') as mock_device:
        svc.forward_ports()
    mock_device.return_value.open_port.assert_not_called()


def test_forward_ports_skips_when_cert_missing(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg), \
                patch('haproxy_service.os.path.exists', return_value=False), \
                patch('haproxy_service.Device') as mock_device:
            svc.forward_ports()
        mock_device.return_value.open_port.assert_not_called()
    finally:
        os.unlink(cfg)


def test_forward_ports_opens_four_ports(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg), \
                patch('haproxy_service.os.path.exists', return_value=True), \
                patch('haproxy_service.Device') as mock_device:
            svc.forward_ports()
        calls = mock_device.return_value.open_port.call_args_list
        ports = [c[0][0] for c in calls]
        assert 443 in ports
        assert 5222 in ports
        assert 4244 in ports
        assert 6443 in ports
    finally:
        os.unlink(cfg)


# ── self_test / _probe_port ───────────────────────────────────────────────────

def test_self_test_passes_when_disabled(svc):
    with patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'), \
            patch('haproxy_service.socket.create_connection') as mock_conn:
        assert svc.self_test() is True
    mock_conn.assert_not_called()


def test_self_test_skips_when_not_running(svc):
    with patch.object(svc, 'is_running', return_value=False), \
            patch('haproxy_service.socket.create_connection') as mock_conn:
        assert svc.self_test() is True
    mock_conn.assert_not_called()


def test_self_test_returns_true_when_all_ports_ok(svc):
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda s: s
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_sock.recv.return_value = b'data'
    with patch.object(svc, 'is_running', return_value=True), \
            patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'), \
            patch('haproxy_service.socket.create_connection', return_value=mock_sock):
        assert svc.self_test() is True


def test_self_test_returns_false_when_port_unreachable(svc):
    with patch.object(svc, 'is_enabled', return_value=True), \
            patch.object(svc, 'is_running', return_value=True), \
            patch('haproxy_service.CONFIG_FILE', '/nonexistent/path'), \
            patch('haproxy_service.socket.create_connection',
                  side_effect=ConnectionRefusedError):
        assert svc.self_test() is False


def test_probe_port_true_with_server_greeting(svc):
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda s: s
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_sock.recv.return_value = b'<?xml version'
    with patch('haproxy_service.socket.create_connection', return_value=mock_sock):
        assert svc._probe_port(5222) is True


def test_probe_port_true_when_no_greeting(svc):
    mock_sock = MagicMock()
    mock_sock.__enter__ = lambda s: s
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_sock.recv.side_effect = socket.timeout
    with patch('haproxy_service.socket.create_connection', return_value=mock_sock):
        assert svc._probe_port(443) is True


def test_probe_port_false_when_connection_refused(svc):
    with patch('haproxy_service.socket.create_connection',
               side_effect=ConnectionRefusedError):
        assert svc._probe_port(5222) is False


def test_forward_ports_uses_custom_ports(svc):
    cfg = _temp_config("[haproxy]\nenabled = 1\nport_ssl = 8443\nport_a = 1234\nport_b = 5678\nport_signal = 7443\n")
    try:
        with patch('haproxy_service.CONFIG_FILE', cfg), \
                patch('haproxy_service.os.path.exists', return_value=True), \
                patch('haproxy_service.Device') as mock_device:
            svc.forward_ports()
        calls = mock_device.return_value.open_port.call_args_list
        ports = [c[0][0] for c in calls]
        assert 8443 in ports
        assert 1234 in ports
        assert 5678 in ports
        assert 7443 in ports
    finally:
        os.unlink(cfg)
