import sys
import os
import configparser
import pytest
from unittest.mock import MagicMock, patch, call

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
    from periodic.ssl_cert import _is_configured, main, WEPN_RUN, SSL_CERT_CMD_INDEX


def _make_config(
    has_dyndns=True,
    enabled=True,
    method="cloudflare",
    issue_ssl=True,
    token="abc123token",
    hostname="pod.example.com",
):
    config = configparser.ConfigParser()
    if has_dyndns:
        config.add_section("dyndns")
        config.set("dyndns", "enabled", "1" if enabled else "0")
        config.set("dyndns", "method", method)
        config.set("dyndns", "issue_certbot_ssl", "1" if issue_ssl else "0")
        config.set("dyndns", "token", token)
        config.set("dyndns", "hostname", hostname)
    return config


# ── _is_configured ──────────────────────────────────────────────────────────

def test_is_configured_all_valid():
    assert _is_configured(_make_config()) is True


def test_is_configured_no_dyndns_section():
    assert _is_configured(_make_config(has_dyndns=False)) is False


def test_is_configured_disabled():
    assert _is_configured(_make_config(enabled=False)) is False


def test_is_configured_wrong_method():
    assert _is_configured(_make_config(method="ddns")) is False


def test_is_configured_flag_off():
    assert _is_configured(_make_config(issue_ssl=False)) is False


def test_is_configured_empty_token():
    assert _is_configured(_make_config(token="")) is False


def test_is_configured_empty_hostname():
    assert _is_configured(_make_config(hostname="")) is False


# ── main ────────────────────────────────────────────────────────────────────

def test_main_calls_wepn_run_when_configured():
    config = _make_config()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("periodic.ssl_cert.subprocess.run", return_value=mock_result) as mock_run:
        main(config)
    mock_run.assert_called_once_with([WEPN_RUN, "1", SSL_CERT_CMD_INDEX])


def test_main_skips_wepn_run_when_not_configured():
    config = _make_config(issue_ssl=False)
    with patch("periodic.ssl_cert.subprocess.run") as mock_run:
        main(config)
    mock_run.assert_not_called()


def test_main_logs_error_on_wepn_run_failure():
    config = _make_config()
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("periodic.ssl_cert.subprocess.run", return_value=mock_result), \
            patch("periodic.ssl_cert.logger") as mock_logger:
        main(config)
    mock_logger.error.assert_called_once()
    assert 1 in mock_logger.error.call_args[0]


def test_main_no_error_log_on_success():
    config = _make_config()
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("periodic.ssl_cert.subprocess.run", return_value=mock_result), \
            patch("periodic.ssl_cert.logger") as mock_logger:
        main(config)
    mock_logger.error.assert_not_called()


def test_main_warning_logged_for_missing_token():
    config = _make_config(token="")
    with patch("periodic.ssl_cert.subprocess.run") as mock_run, \
            patch("periodic.ssl_cert.logger") as mock_logger:
        main(config)
    mock_run.assert_not_called()
    mock_logger.warning.assert_called_once()
    assert "token" in mock_logger.warning.call_args[0][0]


def test_main_warning_logged_for_missing_hostname():
    config = _make_config(hostname="")
    with patch("periodic.ssl_cert.subprocess.run") as mock_run, \
            patch("periodic.ssl_cert.logger") as mock_logger:
        main(config)
    mock_run.assert_not_called()
    mock_logger.warning.assert_called_once()
    assert "hostname" in mock_logger.warning.call_args[0][0]
