import sys
from unittest.mock import MagicMock

# Global mocks for hardware and external dependencies
# This ensures that any module importing these during testing gets a MagicMock
# instead of failing or using real hardware libraries.


def pytest_configure(config):
    # PIL Mocks
    pil_mocks = [
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFilter',
        'PIL.ImageFont', 'PIL.ImageSequence'
    ]

    # Hardware Mocks
    hardware_mocks = [
        'RPi', 'RPi.GPIO', 'Adafruit_SSD1306', 'adafruit_rgb_display',
        'adafruit_rgb_display.st7789', 'board', 'digitalio', 'busio',
        'pystemd', 'pystemd.systemd1', 'distro', 'netifaces', 'psutil',
        'upnpclient', 'packaging', 'packaging.version', 'qrcode',
        'sqlalchemy', 'sqlalchemy.exc', 'dataset', 'sanitize_filename'
    ]

    all_mocks = pil_mocks + hardware_mocks

    for m in all_mocks:
        if m not in sys.modules:
            sys.modules[m] = MagicMock()

# Ensure that 'lcd' and other modules are fresh if needed
# (though usually conftest.py runs early enough)
