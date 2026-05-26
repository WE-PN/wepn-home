# WEPN Home Device — Claude Guide

## Project Overview

This repo is the Raspberry Pi (Debian package) component of the WEPN ecosystem. The physical device is called a **WEPN Pod**. It runs VPN/tunnel server software (`pproxy`) on an RPi and communicates with the WEPN backend server and mobile app (separate repos at source.we-pn.com).

The package installs to the Pod as `pproxy-rpi`. All Python source lives under `usr/local/pproxy/`.

## Architecture

**Main process:** `usr/local/pproxy/pproxy.py` — long-running daemon, handles MQTT messaging, GPIO, LCD/LED, and orchestrates all services.

**Services:** Each VPN/tunnel backend (Shadowsocks, WireGuard, OpenVPN, Tor, etc.) is a subclass of `Service` (`service.py`). Adding a new service means subclassing `Service` and registering it in `services.py`.

**Local web server:** `usr/local/pproxy/local_server/` — a separate web API (uWSGI/Flask) running on the Pod. It runs as user `wepn-api` (group `wepn-web`), intentionally separate from the main `pproxy` user to limit its access. **Never change file ownership in `local_server/` without checking `permissions.sh` first** — the user/group split is deliberate.

**System services:** `usr/local/pproxy/system_services/` — includes `led_manager.py` (must run as root) and other services that require elevated privileges. `keypad.py` handles physical button input.

**Periodic tasks:** `usr/local/pproxy/periodic/` — cron-invoked scripts that run outside the main pproxy process: `send_heartbeat.py`, `ddns.py`, `forward_ports.py`, `recovery.py`, `update_dydns.py`, and shell scripts for WireGuard and UPnP.

**Config and runtime state:**
- Config: `/etc/pproxy/config.ini` — controls which features are enabled (including LED and buttons, which should be disabled when not running on a real Pod).
- Runtime state: `/var/local/pproxy/` — `status.ini`, `error-log.*`, SQLite databases (`shadow.db`, `tor.db`). These files change frequently and are not in this repo.

**Logging:** Use the Python `logging` module via the `LOG_CONFIG` constant. Never use bare `print()` for operational logging.

## File Ownership Rules

The `permissions.sh` script defines the authoritative ownership model:
- Most files: `pproxy:pproxy`
- `local_server/` files: `wepn-api:wepn-web`
- `system_services/led_manager.py`: `root:root`
- SQLite DBs (`shadow.db`, `tor.db`): `pproxy:shadow-runners`

**Do not run `chown -R` over the whole project** — it will break the intentional multi-user setup.

## Tests

### Unit Tests

`usr/local/pproxy/tests/units/` — formal tests with mocks for all hardware and external dependencies. These run on any machine.

**Virtual environment:** `usr/local/pproxy/.venv` (all packages pre-installed)

```bash
cd usr/local/pproxy
source .venv/bin/activate
python tests/run_tests_with_coverage.py
```

### Regression / Smoke Tests

`usr/local/pproxy/tests/regression/` — end-to-end smoke tests that verify basic Pod operation. **Can only run on a real Pod.** They have their own venv at `usr/local/pproxy/tests/regression/regenv/`.

**Running:**
```bash
cd usr/local/pproxy/tests/regression
bash run_tests.sh          # single run — handles unclaim and wepn-api reset automatically
bash run_tests_debug.sh    # 10 consecutive runs with per-run logs and analysis
```

**Critical constraints when modifying regression tests:**

- **`pytest-dependency` + `pytest-retry` conflict:** When a test that other tests depend on fails its first attempt and is retried, `pytest-dependency` still marks it as failed and skips all downstream tests — even if the retry ultimately passes. This causes the effective test count to silently collapse from 24 to 8. Tests that gate large dependency chains (`test_login`, `test_claim`) must never rely on retry to pass — use `wait_until` internally or ensure the environment is stable before pytest starts. This is why `run_tests.sh` and `run_tests_debug.sh` sleep **180s** after the final `wepn-run 1 1` before running pytest (see the MQTT race condition note below).

- **MQTT key race condition (`test_check_device_connected`):** When wepn-main starts in unclaimed state, `onboard.py` first tries each of the 5 cached `previous_keys` against the MQTT broker (`onboard-timeout=10s × 5 keys ≈ 50s`), then generates a fresh random key and loops with that. If pytest starts too soon after `wepn-run 1 1`, `test_claim` may register an old cached key with the backend's MQTT broker while pproxy has already moved on to a different key — the device then never connects. The 180s sleep (covering the full previous-key cycle plus margin) ensures pproxy is stably looping with its fresh key before `test_claim` reads `status.ini temporary_key` and sends it. **Do not add a key-sync step** that overwrites `temporary_key` with `config.ini device_key` — that re-introduces the same race by substituting a stale key that pproxy is no longer using.

- **MQTT propagation delays:** pproxy learns about friend add/delete and claim/unclaim events via MQTT, then writes results to `shadow.db` and `status.ini`. These side-effects can lag 30–180+ seconds behind the backend API call. Tests that poll for them use `wait_until(..., timeout=240)` — do not reduce these timeouts; they are not being conservative.

- **wepn-api `exposed` flag:** `test_simulate_web_exposure` (the last test) sets a module-level `exposed=True` in the Flask/uWSGI process which causes all auth-protected local API endpoints to return 503 instead of 401. This persists until the process restarts. `run_tests.sh` calls `wepn-run 1 1` before the suite specifically to reset this. Unexpected 503s from `https://127.0.0.1:5000` in tests are almost always this flag — restart wepn-api to clear it.

- **OAuth rate limiting:** Running 6+ consecutive full-suite iterations in quick succession can trigger the backend's rate limiter on the test account's OAuth endpoint, causing `test_login` to fail with `KeyError: 'access_token'` on all attempts. `run_tests_debug.sh` includes a 60s inter-run cooldown for this reason. If you see this in a single `run_tests.sh` invocation, wait 5–10 minutes before retrying.

Detailed flakiness analysis and per-test fix history: `tests/regression/handoff.md`.

### Runners (Development Aids)

`usr/local/pproxy/tests/runners/` — informal proof-of-concept scripts used during development to validate a specific area (e.g., `shadow_diag.py`, `test_mqtt.py`, `lcd_test.py`). These are **not real tests**: no mocks required, no formal structure. When developing a feature, you can freely add new runner scripts here or update existing ones to validate your work in progress.

## Development on a Real Pod vs. Non-Pod

If developing on a real WEPN Pod (dev device), LED and physical buttons are available. When developing on a non-Pod machine, disable LED and button features in `/etc/pproxy/config.ini`.

**`constants.py` has one environment-sensitive value:** `LOG_CONFIG` must be `/etc/pproxy/logging-debug.ini` on a dev device and `/etc/pproxy/logging.ini` on production. **Do not commit the debug value** — always verify `LOG_CONFIG` is set to the production path before committing changes to `constants.py`.

## Branch Workflow

- `dev` is the main working branch — all daily development happens here.
- `master` is for tagged releases only.
- Feature branches should PR into `dev`, not `master`.

## Build & Deploy

CI/CD handles building the Debian package and pushing it to Pods. There is no manual build step.

## Pre-commit Hooks

Configured in `.pre-commit-config.yaml`:
- **flake8** — linting (excludes `tests/`)
- **bandit** — security scan (excludes `tests/`)
- **safety** — dependency vulnerability check
- **check-added-large-files**, **detect-private-key**, **debug-statements**

## Module Reference

### Entry Points

| File | Purpose |
|---|---|
| `run.py` | Boot entry — checks claim status, triggers OTA, launches `pproxy.py` or onboarding |
| `debug.py` | Debug entry — same boot decision tree, used on dev devices |
| `pproxy.py` | Main long-running daemon — MQTT, GPIO, LCD/LED, service orchestration |

### Services

| File | Purpose |
|---|---|
| `service.py` | Abstract base class — lifecycle (`start`/`stop`/`restart`), user/credential management, usage tracking, config |
| `services.py` | Orchestrator — registers and manages all service instances |
| `shadow.py` | Shadowsocks — multi-user, per-user ports, SQLite persistence, usage tracking, link generation |
| `wireguard.py` | WireGuard — peer config generation, `wg://` URI encoding for clients |
| `openvpn.py` | OpenVPN — certificate creation/deletion, server lifecycle |
| `tor.py` | Tor bridge — user registration, port forwarding, bridge link generation |
| `unbounded.py` | Unbounded (Lantern) — entry-bridge service, bandwidth limiting, schedule-based access |
| `ssh.py` | SSH — enabled/disabled state, port config, secure settings |
| `haproxy_service.py` | HAProxy TCP proxy — SSL-terminated proxy for messaging apps (WhatsApp/Signal); cert and haproxy.cfg written exclusively by `issue-ssl-cert.sh`; all frontends use SSL termination, backends plain TCP |
| `ooni.py` | OONI network measurement wrapper |
| `measurement.py` | Measurement collection wrapper |
| `networking.py` | Network configuration wrapper |
| `wifi.py` | WiFi autoconnect configuration |

### Core Infrastructure

| File | Purpose |
|---|---|
| `device.py` | Device identity, UPnP port forwarding, OTA updates, SSH/VNC, system health |
| `heartbeat.py` | Sends device status and diagnostics to backend; manages warm-up period (`HEARTBEATS_TO_WARM`) |
| `diag.py` | Self-diagnostics — connectivity tests, port-forward checks, generates device error codes |
| `messages.py` | Device↔app and device↔backend messaging. Supports E2EE (key set up during onboarding via QR code) and plain text. App can communicate with Pod directly over local network or remotely via backend. |
| `usage.py` | Per-user data usage DB with session and wrap-around tracking |
| `ipw.py` | Fetches and validates the Pod's external IP from WEPN's IP service |
| `wstatus.py` | INI file abstraction (ConfigParser wrapper with dirty-tracking). Being adopted to replace direct `configparser` usage for status files. `debug.py`, `messages.py`, and `run.py` are not yet migrated. |
| `constants.py` | Centralised constants — file paths, timeouts, URLs, ports. `LOG_CONFIG` is the one value that differs by environment (see dev/prod note above). |

### Hardware / UI

| File | Purpose |
|---|---|
| `lcd.py` | LCD rendering — SSD1306 and ST7789 displays; text, icons, QR codes, animated menus |
| `led_client.py` | Socket client to the `led_manager` daemon for RGB LED control |
| `metrics_client.py` | Socket client to the local metrics server for per-port connection counts |
| `echo.py` | Simple echo server used to test port-forward connectivity |

### Directories

| Path | Purpose |
|---|---|
| `local_server/` | Local web API (runs as `wepn-api`) — used for direct Pod↔app communication on the same network |
| `system_services/` | System-level daemons — `led_manager.py` (root), `keypad.py` (button input) |
| `setup/` | Onboarding scripts — `onboard.py` generates the E2EE key pair and displays QR code |
| `periodic/` | Cron scripts that run outside the main process |
| `tests/units/` | Unit tests (run anywhere) |
| `tests/runners/` | Informal dev-time validation scripts — no mocks or formal structure needed |
| `tests/regression/` | Smoke tests (Pod only) |
| `usr/local/sbin/permissions.sh` | Authoritative file ownership definitions |
