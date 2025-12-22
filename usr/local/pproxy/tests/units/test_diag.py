import constants
import pytest
from unittest.mock import patch, mock_open
import socket
import subprocess
import requests
import json
import threading
import os
import sys
from unittest.mock import MagicMock

# Mock modules that might be missing in the test environment BEFORE importing diag
for m in ['getmac', 'pystemd', 'pystemd.systemd1', 'distro', 'netifaces', 'psutil', 'upnpclient', 'packaging', 'packaging.version', 'qrcode', 'Adafruit_SSD1306', 'adafruit_rgb_display', 'board', 'sqlalchemy', 'sqlalchemy.exc', 'dataset', 'digitalio', 'busio', 'sanitize_filename']:
    if m not in sys.modules:
        sys.modules[m] = MagicMock()

constants.DEFAULT_GET_TIMEOUT = 10
constants.CONNECTIVITY_TEST_URLS = ["http://google.com"]

#autopep8: off
# Add project root to sys.path
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../../'
if up_dir not in sys.path:
    sys.path.append(up_dir)
from diag import WPDiag
#autopep8: on


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.side_effect = lambda section, option: {
        ('hw', 'iface'): 'eth0',
        ('django', 'serial_number'): 'SN123',
        ('django', 'device_key'): 'KEY123',
        ('django', 'url'): 'http://example.com'
    }.get((section, option))
    return config


@pytest.fixture
@patch('diag.WStatus')
@patch('diag.Device')
@patch('diag.configparser.ConfigParser')
def wp_diag(mock_config_parser, mock_device, mock_wstatus, mock_logger, mock_config):
    mock_config_parser.return_value = mock_config
    mock_wstatus.return_value.get.return_value = 'True'
    diag = WPDiag(mock_logger)
    diag.status = mock_wstatus.return_value
    diag.device = mock_device.return_value
    return diag


def test_init(mock_logger, mock_config):
    with patch('diag.WStatus') as mock_wstatus, \
            patch('diag.Device') as mock_device, \
            patch('diag.configparser.ConfigParser') as mock_config_parser:
        mock_config_parser.return_value = mock_config
        mock_wstatus.return_value.get.return_value = 'True'

        diag = WPDiag(mock_logger)

        assert diag.logger == mock_logger
        assert diag.claimed == 'True'
        assert diag.iface == 'eth0'
        assert diag.port == 987
        mock_wstatus.assert_called_once_with(mock_logger)
        mock_device.assert_called_once_with(mock_logger)


def test_cleanup(wp_diag):
    wp_diag.cleanup()
    assert wp_diag.shutdown_listener is True


def test_sanitize_str(wp_diag):
    assert wp_diag.sanitize_str("test; rm -rf /") == "'test; rm -rf /'"


@patch('diag.subprocess.Popen')
def test_execute_cmd_success(mock_popen, wp_diag):
    wp_diag.execute_cmd("ls -la")
    mock_popen.assert_called_once_with(['ls', '-la'])


@patch('diag.subprocess.Popen')
def test_execute_cmd_error(mock_popen, wp_diag):
    mock_popen.side_effect = Exception("Failed")
    with pytest.raises(SystemExit):
        wp_diag.execute_cmd("ls -la")
    wp_diag.logger.error.assert_any_call("Error happened in running command:ls -la")


@patch('diag.socket.socket')
def test_open_listener_timeout(mock_socket_class, wp_diag):
    mock_socket = MagicMock()
    mock_socket_class.return_value = mock_socket
    # diag.py says TimeoutError but socket raises socket.timeout on settimeout(30)
    # Actually diag.py catches TimeoutError which is standard in Python 3.10+ for socket timeouts
    mock_socket.accept.side_effect = TimeoutError

    # We need to make sure the loop terminates
    wp_diag.shutdown_listener = False

    # Mocking time to trigger shutdown
    with patch('diag.time.time') as mock_time:
        mock_time.side_effect = [1000, 1200]  # initial, after one loop
        wp_diag.open_listener('localhost', 1234)

    # Verify bind was called with expected host and port
    # Note: open_listener converts port to int
    mock_socket.bind.assert_called_once_with(('localhost', 1234))
    mock_socket.listen.assert_called_once_with(1)
    wp_diag.logger.debug.assert_any_call("listener timed out for port 1234")


@patch('diag.socket.socket')
def test_open_listener_success(mock_socket_class, wp_diag):
    mock_socket = MagicMock()
    mock_socket_class.return_value = mock_socket

    mock_conn = MagicMock()
    mock_addr = ('127.0.0.1', 54321)
    mock_socket.accept.return_value = (mock_conn, mock_addr)
    mock_conn.recv.return_value = b'test'

    # Terminate after one accept
    def side_effect(*args, **kwargs):
        wp_diag.shutdown_listener = True
        return mock_conn, mock_addr
    mock_socket.accept.side_effect = side_effect

    wp_diag.open_listener('localhost', 1234)

    mock_conn.sendall.assert_called_once_with(b'test')
    mock_conn.close.assert_called_once()


@patch('diag.threading.Thread')
def test_open_test_port(mock_thread, wp_diag):
    wp_diag.device.open_port.return_value = True
    result = wp_diag.open_test_port(1234)

    assert wp_diag.shutdown_listener is False
    mock_thread.assert_called_once()
    wp_diag.device.open_port.assert_called_once_with(port=1234, text='pproxy test port', timeout=10)
    assert result is True


def test_close_test_port(wp_diag):
    wp_diag.close_test_port(1234)
    assert wp_diag.shutdown_listener is True
    wp_diag.device.close_port.assert_called_once_with(1234)


@patch('diag.requests.get')
def test_is_connected_to_internet_success(mock_get, wp_diag):
    mock_get.return_value = MagicMock()
    assert wp_diag.is_connected_to_internet() is True


@patch('diag.requests.get')
def test_is_connected_to_internet_failure(mock_get, wp_diag):
    mock_get.side_effect = Exception("No internet")
    assert wp_diag.is_connected_to_internet() is False


@patch('diag.socket.create_connection')
def test_is_connected_to_service_success(mock_conn, wp_diag):
    assert wp_diag.is_connected_to_service() is True


@patch('diag.socket.create_connection')
def test_is_connected_to_service_failure(mock_conn, wp_diag):
    mock_conn.side_effect = Exception("No service")
    assert wp_diag.is_connected_to_service() is False


@patch('diag.socket.create_connection')
@patch('diag.ipw.myip')
def test_can_connect_to_external_port_success(mock_myip, mock_conn, wp_diag):
    mock_myip.return_value = '1.1.1.1'
    assert wp_diag.can_connect_to_external_port(80) is True

    mock_conn.side_effect = OSError
    assert wp_diag.can_connect_to_external_port(80) is False


@patch('diag.ipw.myip')
@patch('diag.requests.post')
def test_request_port_check_success(mock_post, mock_myip, wp_diag):
    mock_myip.return_value = '1.1.1.1'
    wp_diag.device.get_all_port_mappings.return_value = (['80'], 1)
    wp_diag.device.get_installed_package_version.return_value = '1.0'
    wp_diag.device.igd_names = ['igd1']

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'id': 123}
    mock_post.return_value = mock_response

    result = wp_diag.request_port_check(80)
    assert result == 123


@patch('diag.requests.post')
def test_fetch_port_check_results_success(mock_post, wp_diag):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'result': 'ok'}
    mock_post.return_value = mock_response

    result = wp_diag.fetch_port_check_results(123)
    assert result == {'result': 'ok'}


@patch('diag.requests.post')
def test_fetch_port_check_results_failure(mock_post, wp_diag):
    mock_post.side_effect = requests.exceptions.RequestException("error")
    assert wp_diag.fetch_port_check_results(123) is None

    mock_post.side_effect = Exception("other error")
    assert wp_diag.fetch_port_check_results(123) is None


@patch('diag.WPDiag.fetch_port_check_results')
def test_get_results_from_server_finished(mock_fetch, wp_diag):
    wp_diag.status.get_field.return_value = 123
    mock_fetch.return_value = {
        'finished_time': '2023-01-01',
        'result': {'experiment_result': 'True'}
    }

    with patch.object(wp_diag, 'close_test_port') as mock_close:
        result = wp_diag.get_results_from_server(80)
        assert result is False
        mock_close.assert_called_once_with(80)
        wp_diag.status.set_field.assert_any_call("port_check", "pending", False)

    # Test KeyError path
    mock_fetch.return_value = {}
    assert wp_diag.get_results_from_server(80) is False

    # Test finished_time is None path
    mock_fetch.return_value = {'finished_time': None}
    assert wp_diag.get_results_from_server(80) is True


@patch('diag.dateutil.parser.parse')
@patch('diag.datetime')
@patch('diag.WPDiag.get_results_from_server')
def test_perform_server_port_check_pending(mock_get_results, mock_datetime, mock_parser, wp_diag):
    wp_diag.status.has_section.return_value = True
    wp_diag.status.get_field.side_effect = lambda s, f: 'True' if f == 'pending' else '2023-01-01'

    import datetime as dt
    now = dt.datetime(2023, 1, 1, 12, 0, 0)
    mock_datetime.datetime.now.return_value = now
    mock_datetime.timedelta = dt.timedelta

    last_check = dt.datetime(2023, 1, 1, 11, 55, 0)
    mock_parser.return_value = last_check

    wp_diag.perform_server_port_check(80)
    mock_get_results.assert_called_once_with(80)


@patch('diag.dateutil.parser.parse')
@patch('diag.datetime')
@patch('diag.WPDiag.open_test_port')
@patch('diag.WPDiag.request_port_check')
@patch('diag.WPDiag.get_results_from_server')
@patch('diag.time.sleep')
def test_perform_server_port_check_expired(mock_sleep, mock_get_results, mock_request, mock_open, mock_datetime, mock_parser, wp_diag):
    wp_diag.status.has_section.return_value = True
    wp_diag.status.get_field.side_effect = lambda s, f: 'False' if f == 'pending' else '2023-01-01'

    import datetime as dt
    now = dt.datetime(2023, 1, 1, 12, 0, 0)
    mock_datetime.datetime.now.return_value = now
    mock_datetime.timedelta = dt.timedelta

    # Long term expired (more than 6 hours ago)
    last_check = dt.datetime(2023, 1, 1, 5, 0, 0)
    mock_parser.return_value = last_check

    mock_request.return_value = 456
    mock_get_results.side_effect = [True, False]  # First call remains pending, second call finishes

    wp_diag.perform_server_port_check(80)

    mock_open.assert_called_with(80)
    mock_request.assert_called_with(80)
    wp_diag.status.set_field.assert_any_call("port_check", "experiment_number", 456)


@patch('diag.socket.create_connection')
def test_can_connect_to_internal_port_success(mock_conn, wp_diag):
    wp_diag.device.get_local_ip.return_value = '192.168.1.1'
    mock_s = MagicMock()
    mock_conn.return_value = mock_s

    assert wp_diag.can_connect_to_internal_port(80) is True
    mock_s.sendall.assert_called_once_with(b'test\n')

    # OSError path
    mock_conn.side_effect = OSError
    assert wp_diag.can_connect_to_internal_port(80) is False


@patch('services.Services')
def test_services_self_test_success(mock_services, wp_diag):
    mock_services.return_value.self_test.return_value = True
    assert wp_diag.services_self_test() is True

    # Exception path
    mock_services.side_effect = Exception("error")
    assert wp_diag.services_self_test() is False


def test_get_error_code(wp_diag):
    wp_diag.device.get_local_ip.return_value = '192.168.1.100'
    with patch.object(wp_diag, 'is_connected_to_internet', return_value=True), \
            patch.object(wp_diag, 'is_connected_to_service', return_value=True), \
            patch.object(wp_diag, 'services_self_test', return_value=True), \
            patch.object(wp_diag, 'perform_server_port_check'):

        wp_diag.status.get.side_effect = lambda k: '1'  # claimed, mqtt
        wp_diag.status.get_field.return_value = 'True'  # port_check result

        assert wp_diag.get_error_code(80) == 127

        # Test claimed = 0
        wp_diag.status.get.side_effect = lambda k: '0' if k == 'claimed' else '1'
        # Total = 1 + 2 + 4 + 8 + 16 + 32 + 0 = 63
        # Wait, if claimed is 0, port check is not performed.
        # But status.get_field('port_check', 'result') might still return 'True' from previous.
        assert wp_diag.get_error_code(80) == 63


@patch('diag.requests.post')
def test_get_server_diag_analysis_success(mock_post, wp_diag):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'analysis': 'all good'}
    mock_post.return_value = mock_response

    result = wp_diag.get_server_diag_analysis(127)
    assert result == {'analysis': 'all good'}

    # Exception path
    mock_post.side_effect = requests.exceptions.RequestException("error")
    assert wp_diag.get_server_diag_analysis(127) is None


@patch('diag.socket.socket')
def test_check_port_locally_in_use(mock_socket_class, wp_diag):
    mock_socket = MagicMock()
    mock_socket_class.return_value = mock_socket
    mock_socket.connect_ex.return_value = 0  # success means port is open

    assert wp_diag.check_port_locally_in_use(80) is True

    # Closed path
    mock_socket.connect_ex.return_value = 1
    assert wp_diag.check_port_locally_in_use(80) is False


def test_check_port_in_blocked(wp_diag):
    assert wp_diag.check_port_in_blocked(5000) is True
    assert wp_diag.check_port_in_blocked(80) is False


@patch('diag.WPDiag.open_test_port')
@patch('diag.WPDiag.request_port_check')
@patch('diag.WPDiag.fetch_port_check_results')
@patch('diag.time.sleep')
def test_find_next_good_port_success(mock_sleep, mock_fetch, mock_request, mock_open, wp_diag):
    wp_diag.device.check_port_mapping_igd.return_value = True
    wp_diag.check_port_locally_in_use = MagicMock(return_value=False)
    wp_diag.check_port_in_blocked = MagicMock(return_value=False)
    mock_open.return_value = True
    mock_request.return_value = 123
    mock_fetch.return_value = {
        'completed': 'True',
        'result': {'experiment_result': 'True'}
    }

    with patch.object(wp_diag, 'close_test_port'):
        port, errno = wp_diag.find_next_good_port(1000)
        assert port == 1000
        assert errno == 0


@patch('diag.WPDiag.open_test_port')
@patch('diag.WPDiag.request_port_check')
@patch('diag.WPDiag.fetch_port_check_results')
@patch('diag.time.sleep')
def test_find_next_good_port_retries_and_success(mock_sleep, mock_fetch, mock_request, mock_open, wp_diag):
    wp_diag.device.check_port_mapping_igd.return_value = True
    wp_diag.check_port_locally_in_use = MagicMock(side_effect=[True, False, False])
    wp_diag.check_port_in_blocked = MagicMock(side_effect=[True, False, False])

    # First port (1000) is blocked -> errno 1, retries 1, port 1001
    # Second port (1001) is in use -> errno 2, retries 2, port 1002
    # Third port (1002) is good

    mock_open.return_value = True
    mock_request.return_value = 123
    mock_fetch.return_value = {
        'completed': 'True',
        'result': {'experiment_result': 'True'}
    }

    with patch.object(wp_diag, 'close_test_port'):
        port, errno = wp_diag.find_next_good_port(1000)
        assert port == 1002
        assert errno == 0


def test_find_next_good_port_no_igd(wp_diag):
    wp_diag.device.check_port_mapping_igd.return_value = False
    port, errno = wp_diag.find_next_good_port(1000)
    assert port == 1000
    assert errno == 404
