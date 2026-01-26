import pytest
import socket
import os
import sys
from unittest.mock import MagicMock, patch

# To handle top-level fileConfig on import
with patch('logging.config.fileConfig'):
    import led_client
    from led_client import LEDClient


@pytest.fixture
def mock_socket():
    with patch('socket.socket') as m:
        mock_instance = MagicMock()
        m.return_value = mock_instance
        yield mock_instance


def test_init_success(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        assert lc.client is not None
        mock_socket.connect.assert_called_with(led_client.LM_SOCKET_PATH)


def test_init_no_socket_file(mock_socket):
    with patch('os.path.exists', return_value=False):
        lc = LEDClient()
        assert lc.client is None


def test_init_permission_error_then_success(mock_socket):
    with patch('os.path.exists', return_value=True), \
            patch('device.Device') as m_device:

        # First call fails, second succeeds
        mock_socket.connect.side_effect = [PermissionError, None]

        lc = LEDClient()
        assert lc.client is not None
        m_device.return_value.execute_setuid.assert_called_with("1 14")
        assert mock_socket.connect.call_count == 2


def test_init_general_exception(mock_socket):
    with patch('os.path.exists', return_value=True):
        mock_socket.connect.side_effect = Exception("error")
        lc = LEDClient()
        assert lc.client is None


def test_del_closes_socket(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        sock = lc.client
        lc.__del__()
        sock.close.assert_called_once()


def test_set_enabled(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.set_enabled(True)
        mock_socket.send.assert_called_with(b"set_enabled 1")
        lc.set_enabled(False)
        mock_socket.send.assert_called_with(b"set_enabled 0")


def test_set_brightness(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.set_brightness(0.7)
        mock_socket.send.assert_called_with(b"set_brightness 0.7")

        # Invalid values
        mock_socket.send.reset_mock()
        lc.set_brightness(1.5)
        mock_socket.send.assert_not_called()
        lc.set_brightness(-0.1)
        mock_socket.send.assert_not_called()


def test_set_all(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.set_all((10, 20, 30))
        mock_socket.send.assert_called_with(b"set_all 10 20 30")


def test_blank(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.blank()
        mock_socket.send.assert_called_with(b"blank")


def test_rainbow(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.rainbow(10, 50)
        mock_socket.send.assert_called_with(b"rainbow 10 50")


def test_progress_wheel_step(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.progress_wheel_step((255, 0, 0))
        mock_socket.send.assert_called_with(b"progress_wheel_step 255 0 0")


def test_pulse(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.pulse((1, 2, 3), 100, 5)
        mock_socket.send.assert_called_with(b"pulse 1 2 3 100 5")


def test_blink(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.blink((4, 5, 6), 200, 10)
        mock_socket.send.assert_called_with(b"blink 4 5 6 200 10")


def test_spinning_wheel(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.spinning_wheel((7, 8, 9), 50, 5, 3)
        mock_socket.send.assert_called_with(b"spinning_wheel 7 8 9 50 5 3")


def test_progress_wheel(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.progress_wheel((100, 100, 100), 0.75)
        mock_socket.send.assert_called_with(b"progress_wheel 100 100 100 0.75")


def test_fill_upto(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.fill_upto((0, 255, 0), 0.4, 150)
        mock_socket.send.assert_called_with(b"fill_upto 0 255 0 0.4 150")


def test_fill_downfrom(mock_socket):
    with patch('os.path.exists', return_value=True):
        lc = LEDClient()
        lc.fill_downfrom((0, 0, 255), 0.6, 200)
        mock_socket.send.assert_called_with(b"fill_downfrom 0 0 255 0.6 200")


def test_methods_when_no_client(mock_socket):
    with patch('os.path.exists', return_value=False):
        lc = LEDClient()
        # All these should return early without calling send
        lc.set_enabled(True)
        lc.set_brightness(0.5)
        lc.set_all()
        lc.blank()
        lc.rainbow(1, 1)
        lc.progress_wheel_step()
        lc.pulse()
        lc.blink()
        lc.spinning_wheel()
        lc.progress_wheel()
        lc.fill_upto()
        lc.fill_downfrom()
        mock_socket.send.assert_not_called()


def test_main_execution(mock_socket):
    with patch('os.path.exists', return_value=True), \
            patch('time.sleep'), \
            patch('led_client.time.sleep'), \
            patch('logging.config.fileConfig'):

        led_client.main()

        # Verify that some calls were made (since it's a long sequence)
        assert mock_socket.send.called
