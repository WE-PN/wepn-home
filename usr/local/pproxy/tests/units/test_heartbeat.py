import sys
import os
import pytest
from unittest.mock import MagicMock, patch, ANY

#autopep8: off
# Add the parent directory to sys.path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from constants import HEALTHY_DIAG_CODE, HEARTBEATS_TO_WARM, METRICS_REPORT_INTERVAL_SECONDS
#autopep8: on

# Mock EXTERNAL dependencies only
for m in ['qrcode', 'Adafruit_SSD1306', 'getmac', 'pystemd', 'pystemd.systemd1', 'distro', 'netifaces', 'psutil', 'upnpclient', 'packaging', 'packaging.version', 'adafruit_rgb_display', 'adafruit_rgb_display.st7789', 'sanitize_filename', 'RPi', 'RPi.GPIO', 'luma', 'luma.core', 'luma.core.interface', 'luma.core.interface.serial', 'luma.oled', 'luma.oled.device', 'board', 'sqlalchemy', 'sqlalchemy.exc', 'dataset', 'digitalio', 'busio']:
    if m not in sys.modules:
        sys.modules[m] = MagicMock()

# Patch logging.config.fileConfig to avoid loading non-existent config files during import
with patch('logging.config.fileConfig'):
    from heartbeat import HeartBeat


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_dependencies():
    with patch('heartbeat.WStatus') as mock_wstatus, \
            patch('heartbeat.WPDiag') as mock_wpdiag, \
            patch('heartbeat.Services') as mock_services, \
            patch('heartbeat.Measurement') as mock_measurement, \
            patch('heartbeat.MetricsClient') as mock_metrics, \
            patch('heartbeat.configparser.ConfigParser') as mock_config_parser, \
            patch('heartbeat.IPW') as mock_ipw, \
            patch('heartbeat.Device') as mock_device, \
            patch('heartbeat.LCD') as mock_lcd, \
            patch('heartbeat.requests') as mock_requests:

        # Setup common mock behaviors
        mock_config = MagicMock()
        mock_config_parser.return_value = mock_config

        # Default config values
        mock_config.get.side_effect = lambda section, option: {
            ('django', 'serial_number'): 'SN123',
            ('django', 'device_key'): 'KEY123',
            ('django', 'url'): 'http://api.example.com',
            ('django', 'id'): 'DEVICE_ID',
            ('shadow', 'enabled'): '0',
            ('shadow', 'start-port'): '4000',
            ('openvpn', 'port'): '1194',
            ('hw', 'lcd'): '1'
        }.get((section, option), 'mock_value')

        # Default wstatus values
        mock_wstatus.return_value.get.side_effect = lambda key: {
            'hb_to_warm': '0',
            'state': '2',  # Running
            'sw': '1.0.0',
            'local_token': 'TOKEN',
            'pin': '123456'
        }.get(key, 'mock_value')
        mock_wstatus.return_value.status.getint.return_value = 0

        # Default wpdiag values
        mock_wpdiag.return_value.get_error_code.return_value = HEALTHY_DIAG_CODE

        # Default services values
        mock_services.return_value.get_usage_status_summary.return_value = ({})
        mock_services.return_value.get_usage_deltas.return_value = ({})
        mock_services.return_value.get_service_creds_summary.return_value = {}

        # Default device values
        mock_device.return_value.get_local_ip.return_value = "127.0.0.1"
        mock_device.return_value.get_system_health_stats.return_value = {}
        mock_device.return_value.get_all_port_mappings.return_value = ([], 0)

        yield {
            'wstatus': mock_wstatus,
            'wpdiag': mock_wpdiag,
            'services': mock_services,
            'measurement': mock_measurement,
            'metrics': mock_metrics,
            'config': mock_config,
            'ipw': mock_ipw,
            'device': mock_device,
            'lcd': mock_lcd,
            'requests': mock_requests
        }


@pytest.fixture
def heartbeat(mock_logger, mock_dependencies):
    return HeartBeat(mock_logger)


def test_initialization(heartbeat, mock_logger):
    assert heartbeat.logger == mock_logger
    assert heartbeat.mqtt_connected == 0
    assert heartbeat.save_status_immediately is True
    assert isinstance(heartbeat.pin, int)
    assert isinstance(heartbeat.local_token, int)


def test_buffer_status_saves(heartbeat):
    heartbeat.buffer_status_saves(True)
    assert heartbeat.save_status_immediately is False
    heartbeat.buffer_status_saves(False)
    assert heartbeat.save_status_immediately is True


def test_is_connected_via_diag(heartbeat, mock_dependencies):
    mock_dependencies['wpdiag'].return_value.is_connected_to_internet.return_value = True
    assert heartbeat.is_connected() is True
    mock_dependencies['wpdiag'].return_value.is_connected_to_internet.assert_called_once()


def test_is_connected_fallback_success(heartbeat, mock_dependencies):
    # Simulate diag being None or failing (though code structure implies diag is always set in init)
    # But let's mock the instance to be None to test fallback
    heartbeat.diag = None
    mock_dependencies['requests'].get.return_value.status_code = 200
    assert heartbeat.is_connected() is True
    mock_dependencies['requests'].get.assert_called()


def test_is_connected_fallback_failure(heartbeat, mock_dependencies):
    heartbeat.diag = None
    mock_dependencies['requests'].get.side_effect = Exception("Connection error")
    assert heartbeat.is_connected() is False


def test_get_display_string_status_v2_ok(heartbeat, mock_dependencies):
    mock_lcd_instance = MagicMock()
    mock_lcd_instance.version = 2
    mock_lcd_instance.get_status_icons_v2.return_value = ("icons", False, [])

    display_str = heartbeat.get_display_string_status(1, 0, mock_lcd_instance)
    assert "OK" in display_str[1][1]
    assert display_str[0][3] == "green"


def test_get_display_string_status_v2_error(heartbeat, mock_dependencies):
    mock_lcd_instance = MagicMock()
    mock_lcd_instance.version = 2
    # Force any_err logic inside the method (it overrides get_status_icons_v2 return in the code provided?
    # Wait, line 85 sets any_err = False explicitly in the provided code!
    # So it will always be OK unless that line is a bug or I misread.
    # Reading code:
    # 84: icons, any_err, errs = lcd.get_status_icons_v2(status, diag_code)
    # 85: any_err = False
    # So it seems it's hardcoded to False. Let's test that behavior.)

    mock_lcd_instance.get_status_icons_v2.return_value = ("icons", True, ["error"])
    display_str = heartbeat.get_display_string_status(1, 1, mock_lcd_instance)
    assert "OK" in display_str[1][1]  # Because of line 85


def test_get_display_string_status_v1(heartbeat, mock_dependencies):
    mock_lcd_instance = MagicMock()
    mock_lcd_instance.version = 1
    mock_lcd_instance.get_status_icons.return_value = ("icons", False)

    display_str = heartbeat.get_display_string_status(1, 0, mock_lcd_instance)
    assert "PIN: " in display_str[0][1]
    assert display_str[2][3] == "green"


def test_send_heartbeat_success(heartbeat, mock_dependencies):
    # Setup mocks
    mock_dependencies['wstatus'].return_value.status.getint.return_value = 0
    # Ensure get returns string for all keys needed

    mock_dependencies['wpdiag'].return_value.get_error_code.return_value = HEALTHY_DIAG_CODE
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat(lcd_print=False)

    # Verify requests.get called with correct URL
    mock_dependencies['requests'].get.assert_called_once()
    args, kwargs = mock_dependencies['requests'].get.call_args
    assert "api/device/heartbeat/" in args[0]
    assert "data" in kwargs


def test_send_heartbeat_success_rotates_local_token(heartbeat, mock_dependencies):
    mock_dependencies['requests'].get.return_value.ok = True
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat(lcd_print=False)

    mock_dependencies['wstatus'].return_value.set.assert_any_call(
        'local_token', str(heartbeat.local_token))
    mock_dependencies['wstatus'].return_value.set.assert_any_call('prev_token', 'TOKEN')


def test_send_heartbeat_request_exception_does_not_rotate_local_token(heartbeat, mock_dependencies):
    import requests as real_requests
    mock_dependencies['requests'].exceptions.RequestException = real_requests.exceptions.RequestException
    mock_dependencies['requests'].get.side_effect = real_requests.exceptions.RequestException("network unreachable")
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat(lcd_print=False)

    set_calls = mock_dependencies['wstatus'].return_value.set.call_args_list
    assert not any(call.args[0] in ('local_token', 'prev_token', 'pin') for call in set_calls)


def test_send_heartbeat_server_error_does_not_rotate_local_token(heartbeat, mock_dependencies):
    mock_dependencies['requests'].get.return_value.ok = False
    mock_dependencies['requests'].get.return_value.status_code = 500
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat(lcd_print=False)

    set_calls = mock_dependencies['wstatus'].return_value.set.call_args_list
    assert not any(call.args[0] in ('local_token', 'prev_token', 'pin') for call in set_calls)


def test_send_heartbeat_warming(heartbeat, mock_dependencies):
    mock_dependencies['wstatus'].return_value.get.side_effect = lambda k: "10" if k == "hb_to_warm" else "2"
    mock_dependencies['wpdiag'].return_value.get_error_code.return_value = 999  # Not healthy
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat()

    # Verify status sent was 4 (Warming)
    args, kwargs = mock_dependencies['requests'].get.call_args
    data = kwargs['data']
    assert '"status": "4"' in data


def test_send_heartbeat_lcd_update(heartbeat, mock_dependencies):
    # Ensure side_effect handles 'state'
    # It does by default fixture
    mock_dependencies['lcd'].return_value.version = 1
    mock_dependencies['lcd'].return_value.get_status_icons.return_value = ("icons", False)
    mock_dependencies['metrics'].return_value.get_report.return_value = {}

    heartbeat.send_heartbeat(lcd_print=True)

    mock_dependencies['lcd'].return_value.display.assert_called_once()


def test_send_measurements_too_soon(heartbeat, mock_dependencies):
    with patch('heartbeat.datetime') as mock_datetime:
        mock_datetime.now.return_value.timestamp.return_value = 1000
        mock_dependencies['wstatus'].return_value.status.has_option.return_value = True
        mock_dependencies['wstatus'].return_value.status.getint.return_value = 1000 - \
            (METRICS_REPORT_INTERVAL_SECONDS - 10)

        heartbeat.send_measurements()

        mock_dependencies['requests'].post.assert_not_called()

def test_send_measurements_config_disabled(heartbeat, mock_dependencies):
    with patch('heartbeat.datetime') as mock_datetime:
        mock_datetime.now.return_value.timestamp.return_value = 1000
        mock_dependencies['wstatus'].return_value.status.has_option.return_value = True
        mock_dependencies['wstatus'].return_value.status.getint.return_value = 1000 - \
            (METRICS_REPORT_INTERVAL_SECONDS - 10)
        mock_dependencies['measurement'].return_value.get_overlayable_config_value.return_value = False

        heartbeat.send_measurements()

        mock_dependencies['requests'].post.assert_not_called()

def test_send_measurements_success(heartbeat, mock_dependencies):
    with patch('heartbeat.datetime') as mock_datetime:
        mock_datetime.now.return_value.timestamp.return_value = 1000 + METRICS_REPORT_INTERVAL_SECONDS + 10
        mock_dependencies['wstatus'].return_value.status.has_option.return_value = True
        mock_dependencies['wstatus'].return_value.status.getint.return_value = 1000
        mock_dependencies['metrics'].return_value.get_report.return_value = {}
        mock_dependencies['measurement'].return_value.get_overlayable_config_value.return_value = True

        heartbeat.send_measurements()

        mock_dependencies['requests'].post.assert_called_once()
        args, kwargs = mock_dependencies['requests'].post.call_args
        assert "api/device/DEVICE_ID/usage/" in args[0]


def test_send_measurement_and_heartbeat(heartbeat):
    heartbeat.send_measurements = MagicMock()
    heartbeat.send_heartbeat = MagicMock()

    heartbeat.send_measurement_and_heartbeat()

    heartbeat.send_measurements.assert_called_once()
    heartbeat.send_heartbeat.assert_called_once()


def test_record_hb_send(heartbeat, mock_dependencies):
    # Override side_effect to return "5" for hb_to_warm
    mock_dependencies['wstatus'].return_value.get.side_effect = None
    mock_dependencies['wstatus'].return_value.get.return_value = "5"

    heartbeat.record_hb_send()

    mock_dependencies['wstatus'].return_value.set.assert_any_call("hb_to_warm", "4")
    mock_dependencies['wstatus'].return_value.save.assert_called()


def test_record_hb_send_empty_left(heartbeat, mock_dependencies):
    mock_dependencies['wstatus'].return_value.get.side_effect = None
    mock_dependencies['wstatus'].return_value.get.return_value = ""

    heartbeat.record_hb_send()

    mock_dependencies['wstatus'].return_value.set.assert_any_call("hb_to_warm", HEARTBEATS_TO_WARM)
