import sys
import os
import pytest
from unittest.mock import MagicMock, patch

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
    from networking import Networking


@pytest.fixture
def svc():
    with patch('networking.Device'), \
            patch('service.WStatus'), \
            patch('service.Device'), \
            patch('service.CONFIG_FILE', '/nonexistent/path'):
        return Networking(MagicMock())


# ── configure ────────────────────────────────────────────────────────────────

def test_configure_calls_start_when_uplink_changes(svc):
    svc.service_config.get_field.side_effect = [
        'tor', 'geo',   # prev: uplink, routing-mode
        'warp', 'geo',  # new:  uplink, routing-mode
    ]
    with patch.object(svc, 'start') as mock_start:
        svc.configure('{"uplink": "warp"}')
    mock_start.assert_called_once()


def test_configure_calls_start_when_mode_changes(svc):
    svc.service_config.get_field.side_effect = [
        'tor', 'geo',
        'tor', 'all-traffic',
    ]
    with patch.object(svc, 'start') as mock_start:
        svc.configure('{"routing-mode": "all-traffic"}')
    mock_start.assert_called_once()


def test_configure_calls_start_when_both_change(svc):
    svc.service_config.get_field.side_effect = [
        'tor', 'geo',
        'warp', 'all-traffic',
    ]
    with patch.object(svc, 'start') as mock_start:
        svc.configure('{"uplink": "warp", "routing-mode": "all-traffic"}')
    mock_start.assert_called_once()


def test_configure_skips_start_when_nothing_changes(svc):
    svc.service_config.get_field.side_effect = [
        'tor', 'geo',
        'tor', 'geo',
    ]
    with patch.object(svc, 'start') as mock_start:
        svc.configure('{"other": "value"}')
    mock_start.assert_not_called()
