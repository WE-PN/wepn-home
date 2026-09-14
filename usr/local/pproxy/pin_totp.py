import hashlib
import hmac
import secrets
import time

from constants import (PIN_ALPHABET, PIN_LENGTH, PIN_TOTP_STEP_SECONDS,
                       PIN_TOTP_WINDOW, PIN_TOTP_CODE_MIN, PIN_TOTP_CODE_MAX,
                       PIN_TOTP_PURPOSE_LOCAL_TOKEN)

CODE_RANGE = PIN_TOTP_CODE_MAX - PIN_TOTP_CODE_MIN + 1
_ALPHABET_SET = frozenset(PIN_ALPHABET)


def generate_pin() -> str:
    return ''.join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))


def is_valid_pin_format(value) -> bool:
    return bool(value) and len(value) == PIN_LENGTH and set(value) <= _ALPHABET_SET


def current_step(t=None) -> int:
    return int(t if t is not None else time.time()) // PIN_TOTP_STEP_SECONDS


def derive_code(pin: str, step: int, purpose: bytes) -> int:
    msg = int(step).to_bytes(8, 'big') + purpose
    mac = hmac.new(pin.encode('ascii'), msg, hashlib.sha256).digest()
    return PIN_TOTP_CODE_MIN + (int.from_bytes(mac, 'big') % CODE_RANGE)


def derive_local_token(pin: str, t=None) -> int:
    return derive_code(pin, current_step(t), PIN_TOTP_PURPOSE_LOCAL_TOKEN)


def matches_any(pin: str, incoming, t=None, window=PIN_TOTP_WINDOW) -> bool:
    # True if incoming matches derive_local_token at any step in
    # [current-window, current+window] (absorbs both the intended one-step
    # grace window and minor clock drift between Pod and backend)
    step = current_step(t)
    candidates = {str(derive_code(pin, step + off, PIN_TOTP_PURPOSE_LOCAL_TOKEN))
                  for off in range(-window, window + 1)}
    return str(incoming) in candidates
