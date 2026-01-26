import pytest
from unittest.mock import MagicMock, patch
from measurement import Measurement


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_service_deps():
    with patch('service.configparser.ConfigParser') as mock_cp, \
            patch('service.WStatus') as mock_wstatus:
        yield {
            'cp': mock_cp,
            'wstatus': mock_wstatus
        }


def test_measurement_init(mock_logger, mock_service_deps):
    m = Measurement(mock_logger)
    assert m.name == "measurement"
    assert m.logger == mock_logger
    # Verify Service.__init__ was called (via indirect evidence of attributes being set)
    assert hasattr(m, 'config')
    assert hasattr(m, 'wstatus')
    assert hasattr(m, 'service_config')


def test_measurement_inheritance(mock_logger, mock_service_deps):
    m = Measurement(mock_logger)
    # Test a few inherited methods to ensure they exist and behave as expected for a basic Service
    assert m.is_kindness_mode() is False
    assert m.add_user("test", "1.2.3.4", "p", 1, "en") is False
    assert m.self_test() is True
    assert m.backup_restore() is True
