# Backend spec: TOTP-derived `local_token`

Pod-side implementation: [pin-totp-local-token-plan.md](pin-totp-local-token-plan.md),
`usr/local/pproxy/pin_totp.py`. This doc is the contract the backend team needs
to implement/verify `local_token` derivation independently; it does not cover
any backend-side code.

## 1. Column repurposing / mixed-fleet detection

The `pin` column, previously a rotating random numeric value, now holds a
16-character string from a fixed alphabet once a device has migrated:

```
^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{16}$
```

(uppercase letters minus `I`/`O`, digits minus `0`/`1` — this is the same
alphabet `device_key` already uses.)

Devices that have not yet sent a new-format `pin` will still be sending a
legacy value: a random numeric string, or the `'00000000'` fresh-install
placeholder. Detect these as **not yet migrated** by checking the regex above
— do not attempt `local_token` verification against a `pin` that fails it.
The fleet will be mixed for a period; no backend logic should assume full
migration (see §7).

## 2. Transport

No new channel or field name. The existing heartbeat JSON body already sends
`"pin"` on every heartbeat, unconditionally. Upsert it into the device's `pin`
column whenever the incoming value matches the new-format regex above. Do not
overwrite a valid-format `pin` already on file with anything that fails the
regex (that would be a rollback to a legacy/placeholder value, which should
never happen from Pod-side logic but should not be trusted blindly either).

## 3. Exact algorithm

```
time_step = floor(unix_epoch_seconds / 900)             # UTC, 900s = 15 min
message   = time_step.to_bytes(8, 'big') + b'\x02'       # trailing purpose byte
mac       = HMAC-SHA256(key = pin.encode('ascii'), msg = message)
code      = 1111111111 + (int.from_bytes(mac, 'big') % 8888888889)
```

`8888888889 = 9999999999 - 1111111111 + 1` (the size of the 10-digit output
range). The purpose byte (`\x02`) must match exactly — it's a domain
separator in case a second time-based value is ever derived from the same
`pin` later.

## 4. Tolerance window

± 1 step (± 900s) if the backend ever *compares* (not just displays/logs) a
`local_token` against its own derivation. This absorbs the Pod's own grace
window plus minor clock drift between Pod and backend.

## 5. Worked test vectors

Generated from the actual `pin_totp.py` implementation (`pin =
"ABCDEFGHJKLMNPQR"`, i.e. `time_step = timestamp // 900`):

| `pin`              | `unix_timestamp` | `time_step` | `expected_local_token` |
|--------------------|------------------|-------------|-------------------------|
| `ABCDEFGHJKLMNPQR` | `0`              | `0`         | `9580143516`            |
| `ABCDEFGHJKLMNPQR` | `900`            | `1`         | `8930495436`            |
| `ABCDEFGHJKLMNPQR` | `1700000000`     | `1888888`   | `1461373344`            |

Reproduce with:

```python
import pin_totp
pin = "ABCDEFGHJKLMNPQR"
for ts in (0, 900, 1_700_000_000):
    print(pin, ts, pin_totp.derive_local_token(pin, ts))
```

## 6. Column type check

Confirm the `pin` column can store a 16-character alphanumeric string, not
just digits. This repo cannot verify the backend schema — please confirm on
your side before rollout.

## 7. Rollout behavior

The fleet will be mixed for a period: some devices on old firmware will keep
sending legacy random numeric pins (or the `'00000000'` placeholder) until
they receive the firmware update and bootstrap a new-format `pin` on their
first post-update heartbeat. No backend logic should assume every device has
migrated. Devices with a legacy-format `pin` should simply not have
`local_token` verified against them (see §1) — this is not an error state,
just an expected transitional one.
