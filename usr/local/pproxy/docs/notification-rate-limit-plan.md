# Cap spam emails/notifications from repeated device events

## Context

When the Pod gets stuck in a bad state — classically a flapping external IP — a cascade produces unbounded notifications: heartbeat reports the new IP (`heartbeat.py:198`) → backend replays one `add_user` command per friend → the device sends one SMTP email per friend (`pproxy.py:511`, "IP change" body from `shadow.py:465`) plus one `user_added` push message (`pproxy.py:523`). N friends × every flap cycle, with **no cooldown, dedup, or cap anywhere** on the device. Same lack of throttle applies to `delete_user` emails/pushes (`pproxy.py:556/567`) and app-response messages (`pproxy.py:427/435`).

Goal: device-side rate limiting so a stuck state can't spam users. Backend fixes may come later; this protects now.

Decisions made with the user:
- **Policy**: per-(recipient, kind) cooldown + global daily cap: **4h email / 1h push / 40 notifications per day**, plus **exact-duplicate suppression** (skip re-sending an unchanged server address even after cooldown expiry).
- **App responses** (`response-access-link`, `response-error-logs`): short anti-replay cooldown, **configurable, default 60s**.
- **Fix the inverted email bodies** in `shadow.py:457` in this same change.
- New-friend adds bypass the cooldown (first email always goes out) but still count toward the daily cap.

## Design

### New file: `usr/local/pproxy/notify_limiter.py`

Small helper class, no new dependencies:

```python
class NotificationLimiter:
    def __init__(self, logger, status):  # status = shared WStatus instance
    def allow(self, kind, recipient, cooldown, token=None) -> bool
```

`allow()` is check-and-record, in order:
1. `now = int(time.time())`; key = `kind + "_" + sha256(recipient)[:12]` (no PII in status.ini, avoids configparser key issues); stored value format `"<last_sent_epoch>|<token>"`.
2. **Dedup**: if `token` matches stored token and entry younger than `NOTIFY_ENTRY_TTL_SECONDS` → log suppression, return False.
3. **Cooldown**: if `0 <= now - last_sent < cooldown` → log suppression, return False. If `now < last_sent` (clock went backwards — RPi has no RTC, pre-NTP boots happen): warn and **allow** (fail open, bounded by daily cap).
4. **Daily cap**: `daily_window_start`/`daily_count` keys; roll window when `now - start >= 86400` or `now < start`. If `daily_count >= NOTIFY_DAILY_LIMIT` → log suppression, return False.
5. Record (before the actual send — an SMTP failure consumes the slot, which is the safe direction during a storm): write entry, bump `daily_count`, prune (drop entries older than TTL, oldest-first eviction above `NOTIFY_MAX_ENTRIES`), `status.save()`, return True.

State lives in a new `[notify_limits]` section of `/var/local/pproxy/status.ini` via the existing `WStatus` (`wstatus.py`) — atomic whole-file replace already; no locking changes. All call sites run on the single slow-dispatch worker thread (`add_user`/`delete_user` are in `SLOW_ACTIONS`, `pproxy.py:59`), so no new concurrency inside the limiter.

### Constants: `usr/local/pproxy/constants.py`

```python
NOTIFY_EMAIL_COOLDOWN_SECONDS = 14400    # 4h
NOTIFY_PUSH_COOLDOWN_SECONDS = 3600      # 1h
NOTIFY_RESPONSE_COOLDOWN_SECONDS = 60    # default; overridable via config.ini
NOTIFY_DAILY_LIMIT = 40
NOTIFY_ENTRY_TTL_SECONDS = 604800        # 7 days
NOTIFY_MAX_ENTRIES = 200
```

The response cooldown is read from `config.ini` (`notify` section, `response-cooldown` key) with `NOTIFY_RESPONSE_COOLDOWN_SECONDS` as fallback, so it's tunable per device without a code change.

### Changes to `usr/local/pproxy/pproxy.py`

- `__init__` (after `self.status = WStatus(...)` ~:101): create `self.notify_limiter = NotificationLimiter(...)`.
- **add_user branch** (~:485–524):
  - Initialize `is_new_user = True` before the inner `try` (currently unbound if `services.add_user` raises before assignment — the :507 block would NameError via `send_email` path).
  - Gate the email (:511): `cooldown = 0 if is_new_user else NOTIFY_EMAIL_COOLDOWN_SECONDS`, `token = None if is_new_user else server_address`, recipient = `data['email'] + "|" + username` (friends sharing an email don't shadow each other). Send only if `allow("add_email", recipient, cooldown, token)`.
  - Gate the `user_added` push (:523) the same way: kind `user_added_msg`, recipient `username`, `NOTIFY_PUSH_COOLDOWN_SECONDS`, same token rule.
  - Leave `update_dns` (:490) **ungated** — DNS must still update on every real IP change.
- **delete_user branch**: gate `send_mail` (:556) with `allow("delete_email", data['email'] + "|" + username, NOTIFY_EMAIL_COOLDOWN_SECONDS)` and `send_msg` (:567) with `allow("user_deleted_msg", username, NOTIFY_PUSH_COOLDOWN_SECONDS)`. The actual `services.delete_user` stays ungated.
- **Response messages** (:427 `response-access-link`, :435 `response-error-logs`): gate with the configurable response cooldown, recipient = `cname`.
- `send_mail` (:283) and `messages.py` signatures unchanged — enforcement is at call sites only.

### Fix inverted email bodies: `usr/local/pproxy/shadow.py:457`

`get_add_email_text` has the branches swapped: `if not is_new_user` currently produces the "You have been granted access" (new-user) text, and the `else` produces "Your access link … is updated … might be due to an IP change". Flip the condition so new users get the welcome text and existing users get the update text. (`services.add_user` → `shadow.py:96` returns `True` for genuinely new users; `pproxy.py:487` already interprets it that way.)

## Tests

**New `usr/local/pproxy/tests/units/test_notify_limiter.py`** — real `WStatus` over a tempfile (pattern from `test_wstatus.py`), patch `time.time`:
- first allow → True + persisted; repeat within cooldown → False; after cooldown → True
- same token after cooldown but within TTL → False (dedup); changed token → True
- `cooldown=0` (new user) passes cooldown but counts toward / is blocked by daily cap
- daily cap hit → False; +24h → window resets
- persistence: new limiter over same file still suppresses (restart survival)
- clock backwards → allowed with warning; window reset
- pruning: TTL expiry and `NOTIFY_MAX_ENTRIES` bound
- kinds independent per recipient

**Extend `usr/local/pproxy/tests/units/test_pproxy.py`** (setUp already patches `pproxy.WStatus`/`pproxy.Messages`; add a `NotificationLimiter` patch so existing tests keep passing):
- limiter denies → no `send_mail`, no `send_msg`; `services.add_user`/`update_dns` still called
- new user → limiter called with cooldown 0 / no token
- limiter allows → mail sent with unchanged args
- delete_user deny path

**Extend/adjust shadow tests** for the flipped `get_add_email_text` branches if any assert the old (inverted) text.

## Verification

1. Run unit suite: `cd /var/local/pproxy/git/home_device/usr/local/pproxy && python tests/run_tests_with_coverage.py` (per dev-Pod memory: no venv activation; use `wepn-env pytest` if needed).
2. On the dev Pod, simulate the storm: send repeated `add_user` for an existing friend (e.g. via the runner scripts in `tests/runners/` or a small new runner) and confirm: first send goes out, repeats are suppressed with a log line in the wepn log, `[notify_limits]` section appears in `/var/local/pproxy/status.ini`, and after `wepn-run 1 1` restart the suppression still holds.
3. Confirm a brand-new friend add still emails immediately.
4. flake8/bandit pre-commit hooks pass; `constants.py` `LOG_CONFIG` still points at the production path before any commit.

## Files touched

- `usr/local/pproxy/notify_limiter.py` (new)
- `usr/local/pproxy/constants.py`
- `usr/local/pproxy/pproxy.py`
- `usr/local/pproxy/shadow.py` (condition flip)
- `usr/local/pproxy/tests/units/test_notify_limiter.py` (new), `tests/units/test_pproxy.py`, shadow email-text tests if present
