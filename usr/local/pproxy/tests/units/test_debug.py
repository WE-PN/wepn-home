import pytest
import sys
from unittest.mock import MagicMock, patch


@pytest.fixture
def base_mocks():
    """Provides common patches for debug.py tests."""
    with patch('logging.config.fileConfig'), \
            patch('logging.getLogger') as m_logger, \
            patch('lcd.LCD') as m_lcd, \
            patch('configparser.ConfigParser') as m_cp:

        # Setup logger mock
        m_logger.return_value = MagicMock()

        # Setup config mock
        cfg_mock = MagicMock()
        cfg_mock.get.side_effect = lambda sec, val: 'True' if val == 'lcd' else '1'
        m_cp.return_value = cfg_mock

        yield {
            'logger': m_logger.return_value,
            'lcd': m_lcd,
            'cp': m_cp,
            'cfg': cfg_mock
        }


def test_debug_execution_claimed(base_mocks):
    m_pproxy = MagicMock()
    with patch.dict('sys.modules', {
        'pproxy': m_pproxy,
        'lcd': MagicMock(),
        'setup.onboard': MagicMock()
    }):
        # Mock status: claimed=1
        base_mocks['cfg'].get.side_effect = lambda sec, val: '1' if val == 'claimed' else 'True'

        import importlib
        if 'debug' in sys.modules:
            importlib.reload(sys.modules['debug'])
        else:
            import debug  # noqa

        # Verify PProxy was started
        m_pproxy.PProxy.assert_called_once_with(base_mocks['logger'])
        m_pproxy.PProxy.return_value.start.assert_called_once()


def test_debug_execution_unclaimed(base_mocks):
    m_onboard_module = MagicMock()
    with patch.dict('sys.modules', {
        'pproxy': MagicMock(),
        'lcd': MagicMock(),
        'setup.onboard': m_onboard_module
    }):
        # Mock status: claimed=0
        base_mocks['cfg'].get.side_effect = lambda sec, val: '0' if val == 'claimed' else 'True'

        import importlib
        if 'debug' in sys.modules:
            importlib.reload(sys.modules['debug'])
        else:
            import debug  # noqa

        # Verify OnBoard was started
        m_onboard_module.OnBoard.assert_called_once()
        m_onboard_module.OnBoard.return_value.start.assert_called_once()


def test_debug_execution_exception(base_mocks):
    m_pproxy = MagicMock()
    # Force exception during PProxy initialization
    m_pproxy.PProxy.side_effect = Exception("test_exception")

    with patch.dict('sys.modules', {
        'pproxy': m_pproxy,
        'lcd': MagicMock(),
        'setup.onboard': MagicMock()
    }):
        # Mock status: claimed=1
        base_mocks['cfg'].get.side_effect = lambda sec, val: '1' if val == 'claimed' else 'True'

        import importlib
        with pytest.raises(Exception, match="test_exception"):
            if 'debug' in sys.modules:
                importlib.reload(sys.modules['debug'])
            else:
                import debug  # noqa
