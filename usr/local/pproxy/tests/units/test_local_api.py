import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../local_server')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))


def _make_wstatus_mock(local_token='8675309', prev_token='', claimed='1',
                       e2e_key='testkey==', temporary_key='tmpkey'):
    mock = MagicMock()
    mock.get_field.side_effect = lambda s, f: {
        ('status', 'local_token'): local_token,
        ('status', 'prev_token'): prev_token,
        ('status', 'claimed'): claimed,
        ('status', 'e2e_key'): e2e_key,
        ('status', 'temporary_key'): temporary_key,
    }.get((s, f), '')
    return mock


@pytest.fixture(scope='module')
def api_module():
    """Import api module once with all module-level dependencies mocked."""
    with patch.dict('sys.modules', {
        'device': MagicMock(),
        'diag': MagicMock(),
        'services': MagicMock(),
        'wstatus': MagicMock(),
    }), patch('configparser.ConfigParser'):
        import importlib
        import api as _api
        importlib.reload(_api)
        yield _api


# ---------------------------------------------------------------------------
# valid_token: correct format and value
# ---------------------------------------------------------------------------

def test_valid_token_matches_stored(api_module):
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock('abc123')):
        assert api_module.valid_token('abc123') is True


def test_valid_token_rejects_wrong_value(api_module):
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock('abc123')):
        assert api_module.valid_token('wrongtoken') is False


def test_valid_token_accepts_prev_token(api_module):
    with patch.object(api_module, 'WStatus',
                      return_value=_make_wstatus_mock('newtoken', prev_token='prevtoken')):
        assert api_module.valid_token('prevtoken') is True


def test_valid_token_rejects_none(api_module):
    assert api_module.valid_token(None) is False


def test_valid_token_rejects_empty(api_module):
    assert api_module.valid_token('') is False


def test_valid_token_rejects_special_chars(api_module):
    """Token with shell metacharacters must be rejected by format check."""
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock("8675309")):
        assert api_module.valid_token("8675309'; rm -rf /") is False


def test_valid_token_rejects_quoted(api_module):
    """Regression guard: shlex-quoted value must NOT match the stored raw token."""
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock('8675309')):
        assert api_module.valid_token("'8675309'") is False


# ---------------------------------------------------------------------------
# claim_info: e2e_key suppressed for claimed devices (Fix P0-E)
# ---------------------------------------------------------------------------

def test_claim_info_claimed_omits_e2e_key(api_module):
    mock_status = _make_wstatus_mock(claimed='1', e2e_key='supersecretkey==')
    with patch.object(api_module, 'WStatus', return_value=mock_status), \
         patch.object(api_module, 'config') as mock_cfg:
        mock_cfg.get.return_value = 'SN-001'
        with api_module.app.test_client() as client:
            resp = client.get('/api/v1/claim/info')
            body = resp.data.decode()
            assert 'supersecretkey==' not in body
            assert 'e2e_key' not in body
            assert 'CLAIMED' in body


def test_claim_info_unclaimed_includes_e2e_key(api_module):
    mock_status = _make_wstatus_mock(claimed='0', e2e_key='supersecretkey==',
                                     temporary_key='tmpkey123')
    with patch.object(api_module, 'WStatus', return_value=mock_status), \
         patch.object(api_module, 'config') as mock_cfg:
        mock_cfg.get.return_value = 'SN-001'
        with api_module.app.test_client() as client:
            resp = client.get('/api/v1/claim/info')
            body = resp.data.decode()
            assert 'supersecretkey==' in body
            assert 'tmpkey123' in body
