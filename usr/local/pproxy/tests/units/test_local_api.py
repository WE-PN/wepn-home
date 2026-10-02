import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../local_server')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import pin_totp  # noqa: E402

TEST_PIN = 'ABCDEFGHJKLMNPQR'


def _make_wstatus_mock(pin=TEST_PIN, claimed='1',
                       e2e_key='testkey==', temporary_key='tmpkey'):
    mock = MagicMock()
    mock.get_field.side_effect = lambda s, f: {
        ('status', 'pin'): pin,
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

def test_valid_token_accepts_current_step(api_module):
    code = pin_totp.derive_local_token(TEST_PIN)
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        assert api_module.valid_token(str(code)) is True


def test_valid_token_rejects_wrong_value(api_module):
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        assert api_module.valid_token('1234567890') is False


def test_valid_token_accepts_previous_step(api_module):
    # grace window: a value derived one step ago must still authenticate
    code = pin_totp.derive_code(TEST_PIN, pin_totp.current_step() - 1,
                                pin_totp.PIN_TOTP_PURPOSE_LOCAL_TOKEN)
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        assert api_module.valid_token(str(code)) is True


def test_valid_token_rejects_two_steps_old(api_module):
    # outside the +/-1 step tolerance window
    code = pin_totp.derive_code(TEST_PIN, pin_totp.current_step() - 2,
                                pin_totp.PIN_TOTP_PURPOSE_LOCAL_TOKEN)
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        assert api_module.valid_token(str(code)) is False


def test_valid_token_rejects_missing_pin(api_module):
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock(pin='')):
        code = pin_totp.derive_local_token(TEST_PIN)
        assert api_module.valid_token(str(code)) is False


def test_valid_token_rejects_legacy_format_pin(api_module):
    # pre-migration devices carry either the '00000000' fresh-install
    # placeholder or an old-style random numeric pin - neither is valid-format
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock(pin='00000000')):
        assert api_module.valid_token('1234567890') is False


def test_valid_token_rejects_none(api_module):
    assert api_module.valid_token(None) is False


def test_valid_token_rejects_empty(api_module):
    assert api_module.valid_token('') is False


def test_valid_token_rejects_special_chars(api_module):
    """Token with shell metacharacters must be rejected by format check."""
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        code = pin_totp.derive_local_token(TEST_PIN)
        assert api_module.valid_token(f"{code}'; rm -rf /") is False


def test_valid_token_rejects_quoted(api_module):
    """Regression guard: shlex-quoted value must NOT match the derived token."""
    with patch.object(api_module, 'WStatus', return_value=_make_wstatus_mock()):
        code = pin_totp.derive_local_token(TEST_PIN)
        assert api_module.valid_token(f"'{code}'") is False


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
