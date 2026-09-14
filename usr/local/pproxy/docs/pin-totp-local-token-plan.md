# TOTP-style derivation for `local_token`, seeded by a permanent onboarding `pin`

_Design doc — implementation deferred; not yet built. See "Critical files" and
"Verification" below when ready to implement._

## Context

`heartbeat.py`'s `HeartBeat.__init__` currently mints a brand-new random `pin`
and `local_token` (`random.SystemRandom().randint(1111111111, 9999999999)`) on
*every* construction. A fresh `HeartBeat()` is built independently from 5+ call
sites with no locking or shared state (`pproxy.py:181,224,710`,
`periodic/send_heartbeat.py:26`, `setup/onboard.py:137`), and `etc/dhcpcd.exit-hook`
also fires `send_heartbeat.py` synchronously on every DHCP event
(`BOUND|RENEW|REBIND|...`) — confirmed by the product owner to be **intended**
(heartbeats are supposed to go out on every IP change), not a bug to remove.

Because two independent invocations each generate their own random value, a race
between e.g. the 15-min cron heartbeat and a dhcpcd-triggered heartbeat can each
push a different value to the backend and to local `status.ini`, permanently
desyncing the Pod's copy from the backend's copy. `local_token` is intended to
rotate every 15 minutes (matching the cron cadence) — that cadence is correct;
the race between independent random generations is the actual bug.

**Chosen fix:** replace random-per-call generation of `local_token` with a
deterministic, time-based (TOTP/RFC 6238-style) derivation:
`local_token = HMAC-SHA256(pin, time_step)` truncated to the existing 10-digit
range, `time_step = unix_time // 900`. Since it's a pure function, any number of
concurrent/independent invocations computing it "at the same moment" agree
exactly — eliminating the race with no locking/debouncing changes anywhere.

**`pin` becomes the permanent seed** (design decided in this planning session,
superseding an earlier draft that used a separate dedicated seed field): instead
of rotating, `pin` is generated **once, at onboarding**, saved, and never updated
again except on `wipe_device`/reclaim — mirroring exactly how `device_key` and
the E2EE key already behave (`setup/onboard.py:82-89`). It is *not* derived from
`device_key` (rejected: `device_key` has only ~50 bits of entropy and is also the
live MQTT password — `setup/onboard.py:209-210` — reusing it would violate
key-domain separation). The backend has an existing, no-longer-used `pin` DB
column that will hold this value directly — no new field name is introduced
anywhere; `heartbeat.py` already sends `"pin"` in its JSON payload on every
heartbeat unconditionally today, so no new transport is needed either.

**Format (decided this session):** 16 characters from the same alphabet
`device_key` already uses — `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (uppercase minus
visually-confusable `I`/`O`, digits minus `0`/`1`) — giving 80 bits of entropy
(`5 bits × 16`). This is a deliberate increase over `device_key`'s 10 characters
(50 bits): as a value that's generated once and never rotates again, `pin` needs
to resist **offline** brute-forcing indefinitely (an attacker who ever observes
one `(timestamp, local_token)` pair could otherwise recover a low-entropy seed
in seconds and predict every future/past `local_token` until reclaim); 80 bits
makes that infeasible.

**No more spoken-support LCD display concern:** the product owner has dropped the
"read the PIN aloud to a support agent" use case, so `pin`'s value no longer needs
to be short or easy to read aloud. The LCD can keep showing it via the existing,
unchanged `get_display_string_status()` code path (`heartbeat.py:82-113`) — it
will just now be static instead of changing every 15 minutes. No further
decision needed here.

**Derivation location (confirmed earlier this session):** on-the-fly in
`local_server/api.py`, not precomputed by `heartbeat.py`. `local_server/api.py`
runs as `wepn-api` and will read `pin` directly from `status.ini` to compute
`derive_local_token(pin, time_step)` itself in `valid_token()`. This needs no
`permissions.sh`/ownership changes: `status.ini` is already `0640
pproxy:shadow-runners`, `wepn-api` is already in the `shadow-runners` group
(`setup/post-install.sh:61-62`), and `api.py` already imports root-level modules
like `device.py`/`wstatus.py` across the user boundary the same way.

This repo (`home_device`) only covers the Pod side; backend derivation logic
lives in a separate repo, so part of this work is a written spec for that team.

## Implementation

### 1. New module `pin_totp.py` (repo root)

Pure, dependency-free (stdlib only: `hmac`, `hashlib`, `secrets`, `time`), no I/O
at import time — safe to import from both `heartbeat.py` (pproxy) and
`local_server/api.py` (wepn-api), the same way `api.py` already imports
`wstatus.py`/`device.py`. Also importable from `setup/onboard.py` for pin
generation, avoiding duplicating the alphabet/format logic in two places. Do
**not** import `heartbeat.py` from `api.py` (it pulls in LCD/GPIO/`IPW()`
module-scope side effects — unnecessary coupling).

```python
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
```

The purpose-byte parameter is kept even though there's currently only one
derived value (`local_token`) — cheap, future-proofs against ever deriving a
second time-based value from the same `pin` later without a collision.

New `constants.py` entries (flat-constant convention, matches existing style):

```python
PIN_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # matches device_key's alphabet
PIN_LENGTH = 16                                     # 80 bits of entropy
PIN_TOTP_STEP_SECONDS = 900                         # 15-minute cadence
PIN_TOTP_WINDOW = 1                                 # ± steps tolerated (grace + clock drift)
PIN_TOTP_CODE_MIN = 1111111111
PIN_TOTP_CODE_MAX = 9999999999
PIN_TOTP_PURPOSE_LOCAL_TOKEN = b"\x02"
```

### 2. `pin` generation & lifecycle — `setup/onboard.py`

Mirror the existing `generate_rand_key()`/`generate_rand_e2e_key()` pattern
(`setup/onboard.py:82-89`) exactly:

- Add `self.rand_pin = None` in `__init__` (alongside `self.rand_key`,
 `self.rand_e2e_key`).
- Add `generate_rand_pin(self): self.rand_pin = pin_totp.generate_pin()`.
- Call it in `start()` alongside the existing `generate_rand_e2e_key()` call
 (~line 251-259).
- In `on_connect()`'s success branch (~line 202-217), alongside the existing
 `self.status.set('status', 'e2e_key', str(self.rand_e2e_key))`, add:
  ```python
  if self.rand_pin is not None:
      self.status.set('status', 'pin', str(self.rand_pin))
  ```
  This is unconditional, exactly like `e2e_key`'s existing line — no
  confirmation-gating needed here, because the *actual* transmission to
  backend happens via the next heartbeat regardless (see Section 3), which
  already has its own race-safe commit logic. This write just makes the pin
  immediately available locally (LCD display, local API) as soon as claim
  succeeds.
- Regeneration on `wipe_device`/reclaim is automatic: `pproxy.py`'s
 `wipe_device` handler (lines 658-668) already sets `claimed=0` and reboots into
 onboarding, which re-runs the above and overwrites `pin` on the next successful
 claim — exactly like `e2e_key` already does. No changes needed there.

### 3. `heartbeat.py` changes

Already-claimed fielded devices predate this change and have a `status.ini`
`pin` field in the *old* format (a random 10-digit number, rewritten every
heartbeat by the current code, or the `'00000000'` fresh-install placeholder
from `setup/update_config.py:80`). `heartbeat.py` must detect this and bootstrap
a new-format `pin` exactly once, using the same race-safe pattern as newly
onboarded devices (both paths converge on the same logic below — no special
casing needed for "new claim" vs. "legacy migration").

**`__init__`** — replace both `random.SystemRandom().randint(...)` lines:

```python
self._pin, self._pin_is_new = self._resolve_pin()
self.local_token = pin_totp.derive_local_token(self._pin)
self.pin = self._pin
```
```python
def _resolve_pin(self):
    existing = self.status.get('pin')
    if pin_totp.is_valid_pin_format(existing):
        return existing, False
    return pin_totp.generate_pin(), True
```
Keep `import random` (still used by `is_connected()`'s `random.shuffle(urls)`).

**`send_heartbeat()` payload** — no change needed to the payload itself:
`"pin": str(self.pin)` already exists and is already sent unconditionally on
every heartbeat (`heartbeat.py:202`). Because `pin` is now static, sending the
same value every 15 minutes (or more often, per the dhcpcd hook) is harmless and
requires no new conditional-inclusion logic — this is what makes reusing the
existing field so clean.

**Post-request persistence** — replace the current `hb_delivered`-gated
pin/local_token block. `local_token` no longer needs backend confirmation to be
*locally* valid (it's a pure function of `pin`+time), so refresh it in
`status.ini` unconditionally. Only a **brand-new bootstrap `pin` candidate**
needs confirmation before being committed, to avoid two racing bootstrap events
(e.g. cron vs. dhcpcd-triggered heartbeat, both firing before any `pin` exists
yet) each committing a different permanent value:

```python
self.status.set('local_token', str(self.local_token))

if hb_delivered:
    if self._pin_is_new:
        # CAS: a concurrent heartbeat may have already committed a bootstrap
        # pin while our own HTTP request was in flight. Re-read before writing.
        self.status.reload()
        if not pin_totp.is_valid_pin_format(self.status.get('pin')):
            self.status.set('pin', self._pin)
        # else: another process already won the race and committed a valid
        # pin first — defer to theirs. Our own next heartbeat will read it as
        # pre-existing (not "new") and stop generating candidates.
else:
    self.logger.error(
        "Heartbeat not confirmed by server; bootstrap pin (if any) not yet persisted")
```

No `prev_token`/`pin_seed_synced`-style flag is needed: once *any* valid-format
`pin` lands in `status.ini`, every subsequent `HeartBeat()` construction reads it
via `_resolve_pin()` and never generates a fresh candidate again — the race
window is exactly, and only, the gap before the very first valid `pin` exists.

**Note (accepted tradeoff):** the CAS reload/recheck has a narrow theoretical
window where two processes could still each "win" the one-time bootstrap race;
it self-heals within one more heartbeat cycle. This only affects the one-time
migration/first-claim event, not steady state, which is fully race-free. A true
`flock` would close it completely but has no precedent in this codebase — not
adding one.

`get_display_string_status()` needs no change — it already reads `self.pin` set
in `__init__`, now static instead of rotating.

### 4. `local_server/api.py` — `valid_token()` (lines 53-60)

```python
import pin_totp

def valid_token(incoming):
    if not incoming or not str(incoming).isalnum():
        return False
    status = WStatus(logger)
    pin = status.get_field('status', 'pin')
    if not pin_totp.is_valid_pin_format(pin):
        return False
    return pin_totp.matches_any(pin, incoming)
```

All 6 existing call sites of `valid_token()` (lines 77, 104, 137, 172, 198, 221)
and the `isalnum()` format guard are unchanged.

### 5. `status.ini` / `setup/update_config.py`

No new field names anywhere — `pin` is reused directly (now holding a 16-char
alphanumeric string instead of a random 10-digit number), `local_token` keeps
its existing digit-string format (now derived, refreshed every heartbeat
unconditionally), `prev_token` is no longer written (harmless to leave stale on
already-deployed files; no active cleanup needed).

`setup/update_config.py`'s existing fresh-install placeholder
(`status.set('status', 'pin', '00000000')`, line 80) needs **no change** — it
already fails `is_valid_pin_format()` (wrong length/charset), so it correctly
triggers bootstrap generation on first heartbeat exactly like a legacy
already-claimed device's leftover 10-digit value would.

### 6. Backend spec deliverable (written doc, not code)

Produce a standalone spec for the backend team covering:
1. **Column repurposing / mixed-fleet detection** — `pin` column now holds a
  16-char string from a fixed alphabet (`^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{16}$`)
  instead of a rotating random numeric pin. Detect legacy/placeholder values
  (numeric strings, or `'00000000'`) as "not yet migrated" — do not attempt
  `local_token` verification for those devices.
2. **Transport** — no new channel or field name: the existing heartbeat JSON
  body's `"pin"` field, sent on every heartbeat, is upserted into the device's
  `pin` column whenever it matches the new-format regex above.
3. **Exact algorithm** (byte-level): `time_step = floor(unix_epoch_seconds /
  900)` (UTC); `message = time_step.to_bytes(8, 'big') + b'\x02'` (trailing
  purpose byte — must match Pod-side exactly); `mac = HMAC-SHA256(key=pin.encode
  ('ascii'), msg=message)`; `code = 1111111111 + (int.from_bytes(mac, 'big') %
  8888888889)`.
4. **Tolerance window** — ± 1 step (900s) if backend ever compares (not just
  displays) a `local_token`.
5. **Worked test vectors** — generate 2-3 `(pin, unix_timestamp,
  expected_local_token)` triples from the real `pin_totp.py` implementation once
  built, and include them verbatim so backend can validate independently.
6. **Column type check** — flag explicitly that backend must confirm the `pin`
  column can store a 16-character alphanumeric string (not just digits) — this
  repo cannot verify the backend's schema.
7. **Rollout behavior** — fleet will be mixed (old firmware still sending
  random legacy pins) for a period; no backend logic should assume full
  migration.

### 7. Tests

**`tests/units/test_heartbeat.py`** — extend `mock_dependencies`'s
`mock_wstatus.return_value.get.side_effect` with a `'pin'` entry set to a fixed
valid-format test pin (16 chars from the alphabet). Use the real `pin_totp`
module with a mocked `time.time()` for determinism (don't mock `pin_totp`
itself — exercise real derivation). Rewrite:
- `test_initialization`: `heartbeat.pin` is now a fixed-format string, not a
 random int — assert `pin_totp.is_valid_pin_format(heartbeat.pin)`; keep
 `isinstance(heartbeat.local_token, int)`.
- `test_send_heartbeat_success_rotates_local_token`: rename/rewrite to assert
 `local_token` is refreshed in status regardless of `hb_delivered`; drop the
 `prev_token` assertion (field no longer written); `pin` is unchanged when
 already valid-format.
- `test_send_heartbeat_request_exception_does_not_rotate_local_token` /
 `..._server_error_...`: assert `local_token` **is** still refreshed
 (unconditional now), but a bootstrap `pin` candidate is **not** persisted on
 failure.

New tests: two independent `HeartBeat()` instances with the same mocked pin/time
produce identical `local_token` (the direct race-regression test); `local_token`
changes across a 900s+ time boundary; bootstrap `pin` candidate persists only
when `hb_delivered=True`; CAS defers to a concurrent writer's `pin` if one
appears on reload; legacy-format existing `pin` (e.g. `'00000000'` or a 10-digit
string) triggers bootstrap exactly like a missing one.

**`tests/units/test_local_api.py`** — `_make_wstatus_mock` needs a `pin` param
(replacing `local_token`/`prev_token`). New tests: accepts current-step derived
code, accepts previous-step derived code (grace window), rejects two-steps-old
code (outside window), rejects missing/legacy-format `pin`. Existing
format-guard tests (`rejects_none`, `rejects_empty`, `rejects_special_chars`,
`rejects_quoted`) are untouched.

Run via `cd usr/local/pproxy && python -m pytest tests/units/` (no venv
activation, matching project convention).

### 8. Optional follow-up (not required for correctness, flagging only)

`periodic/forward_ports.py:39,67` reads `status.ini`'s `local_token` directly to
authenticate its own loopback call — this keeps working transparently since
`heartbeat.py` continues to refresh that field every beat (Section 3), but it's
an indirect dependency on heartbeat having run recently. Could be updated later
to import `pin_totp` and derive directly from `pin`, removing the implicit
coupling — not doing this now, out of scope.

## Critical files

- `usr/local/pproxy/pin_totp.py` (new)
- `usr/local/pproxy/constants.py`
- `usr/local/pproxy/heartbeat.py`
- `usr/local/pproxy/local_server/api.py`
- `usr/local/pproxy/setup/onboard.py`
- `usr/local/pproxy/tests/units/test_heartbeat.py`
- `usr/local/pproxy/tests/units/test_local_api.py`
- Backend spec: new doc, e.g. `usr/local/pproxy/docs/pin-totp-backend-spec.md`

(`setup/update_config.py` needs no change — see Section 5.)

## Verification

**Unit level (any machine, no real backend):**
```
cd usr/local/pproxy && python -m pytest tests/units/test_heartbeat.py tests/units/test_local_api.py -q
```
All HTTP is mocked; this validates derivation math, the CAS bootstrap logic, and
`valid_token()`'s tolerance window in-process.

**Manual Pod-level checklist** (add an informal script under `tests/runners/`,
e.g. `pin_totp_check.py`, per this project's convention for proof-of-concept
validation — no mocks required):
1. On a claimed dev Pod, read `status.ini`'s `pin`, independently compute
  `derive_local_token`, and cross-check against what the local API
  (`https://127.0.0.1:5000/api/v1/...?local_token=...`) actually accepts.
2. **Race regression (the actual bug):** run `python3 periodic/send_heartbeat.py`
  manually while forcing a DHCP renew (`sudo dhclient -r && sudo dhclient`) to
  fire `dhcpcd.exit-hook` concurrently; confirm `status.ini`'s `local_token` does
  not diverge between the two runs.
3. **Rotation:** wait across a 15-minute boundary; confirm `local_token` changes,
  and a value captured just before rotation still authenticates for one grace
  step, then stops working outside the window. Confirm `pin` itself does *not*
  change.
4. **Migration/bootstrap:** set `status.ini`'s `pin` to a legacy-format value
  (e.g. `'00000000'`) on a test device, restart; confirm the next heartbeat
  generates a new-format `pin` and (assuming backend ack) `status.ini` ends up
  with the new `pin` persisted, and it doesn't change again on subsequent beats.
5. **Wipe/reclaim:** trigger `wipe_device`, re-onboard, confirm a *different*
  `pin` is committed and old derived `local_token` values stop validating.
6. **Regression suite:** run `tests/regression/` per `run_tests.sh` on a real Pod
  (per this project's existing constraints on regression-test timing/races,
  documented in `CLAUDE.md`) — since `status.ini`'s `local_token` keeps being
  populated unconditionally every heartbeat, existing local-API regression tests
  should pass unmodified.
