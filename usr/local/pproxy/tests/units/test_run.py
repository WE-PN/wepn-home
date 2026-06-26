import pytest
import sys
import os
import json
import requests  # ensure urllib3/backports are in sys.modules before os.stat is mocked
from unittest.mock import MagicMock, patch, mock_open


@pytest.fixture
def base_mocks():
    """Provides a set of common patches for run.py tests."""
    with patch('logging.config.fileConfig'), \
            patch('logging.getLogger') as m_logger, \
            patch('os.path.exists', return_value=True), \
            patch('os.stat') as m_stat, \
            patch('shutil.copyfile'), \
            patch('configparser.ConfigParser') as m_cp, \
            patch('lcd.LCD') as m_lcd, \
            patch('led_client.LEDClient') as m_leds, \
            patch('device.Device') as m_device, \
            patch('requests.post') as m_post, \
            patch('time.sleep'), \
            patch('datetime.datetime') as m_dt, \
            patch.dict('sys.modules', {'wstatus': MagicMock()}), \
            patch('builtins.open', mock_open()) as m_open:

        m_stat.return_value.st_size = 100

        # Helper to setup a default mock config that won't break json.dumps
        def setup_cfg(cfg_mock):
            cfg_mock.get.side_effect = lambda sec, val: {
                'claimed': '1',
                'url': 'http://test',
                'serial_number': '123',
                'lcd': 'True'
            }.get(val, 'default_val')
            cfg_mock.__getitem__.return_value = MagicMock()
            cfg_mock.__getitem__.return_value.__getitem__.return_value = "mock_val"

        yield {
            'logger': m_logger,
            'cp': m_cp,
            'lcd': m_lcd,
            'leds': m_leds,
            'device': m_device,
            'post': m_post,
            'open': m_open,
            'dt': m_dt,
            'setup_cfg': setup_cfg
        }


def test_check_and_restore(base_mocks):
    with patch.dict('sys.modules', {
        'paho': MagicMock(),
        'paho.mqtt': MagicMock(),
        'paho.mqtt.client': MagicMock(),
        'pad4pi': MagicMock(),
        'pproxy': MagicMock(),
        'setup.onboard': MagicMock()
    }):
        # setup cfg to avoid json.dumps error during import
        cfg = MagicMock()
        base_mocks['setup_cfg'](cfg)
        base_mocks['cp'].return_value = cfg

        from run import check_and_restore

        # Test successful parse
        check_and_restore("conf", "bak", ["sec", "key"])
        base_mocks['logger'].return_value.error.assert_called_with("mock_val")


def test_main_execution_claimed(base_mocks):
    m_pproxy = MagicMock()
    with patch.dict('sys.modules', {
        'paho': MagicMock(),
        'paho.mqtt': MagicMock(),
        'paho.mqtt.client': MagicMock(),
        'pad4pi': MagicMock(),
        'pproxy': m_pproxy,
        'setup.onboard': MagicMock()
    }):
        cfg = MagicMock()
        base_mocks['setup_cfg'](cfg)
        base_mocks['cp'].return_value = cfg

        resp = MagicMock()
        resp.status_code = 200
        base_mocks['post'].return_value = resp
        base_mocks['dt'].now.return_value.timestamp.return_value = 1600000000

        import importlib
        if 'run' in sys.modules:
            importlib.reload(sys.modules['run'])
        else:
            import run  # noqa

        m_pproxy.PProxy.return_value.start.assert_called_once()


def test_main_execution_unclaimed(base_mocks):
    m_onboard_module = MagicMock()
    with patch.dict('sys.modules', {
        'paho': MagicMock(),
        'paho.mqtt': MagicMock(),
        'paho.mqtt.client': MagicMock(),
        'pad4pi': MagicMock(),
        'pproxy': MagicMock(),
        'setup.onboard': m_onboard_module
    }):
        cfg = MagicMock()
        base_mocks['setup_cfg'](cfg)
        # Force claimed=0
        cfg.get.side_effect = lambda sec, val: '0' if val == 'claimed' else 'http://test' if val == 'url' else '123'
        base_mocks['cp'].return_value = cfg

        resp = MagicMock()
        resp.status_code = 404
        base_mocks['post'].return_value = resp

        import importlib
        if 'run' in sys.modules:
            importlib.reload(sys.modules['run'])
        else:
            import run  # noqa

        m_onboard_module.OnBoard.return_value.start.assert_called()


def test_request_exception_retry(base_mocks):
    with patch.dict('sys.modules', {
        'paho': MagicMock(),
        'paho.mqtt': MagicMock(),
        'paho.mqtt.client': MagicMock(),
        'pad4pi': MagicMock(),
        'pproxy': MagicMock(),
        'setup.onboard': MagicMock()
    }):
        cfg = MagicMock()
        base_mocks['setup_cfg'](cfg)
        base_mocks['cp'].return_value = cfg

        import requests
        # Fail first, then succeed
        success_resp = MagicMock()
        success_resp.status_code = 200
        base_mocks['post'].side_effect = [
            requests.exceptions.RequestException("fail"), success_resp]

        import importlib
        if 'run' in sys.modules:
            importlib.reload(sys.modules['run'])
        else:
            import run  # noqa

        assert base_mocks['post'].call_count == 2
        base_mocks['logger'].return_value.error.assert_any_call(
            "Error in connecting to server for claim status")
