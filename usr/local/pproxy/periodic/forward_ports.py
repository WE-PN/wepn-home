import os
import logging.config
import sys

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'  # nopep8 noqa
sys.path.append(up_dir)
from wstatus import WStatus  # nopep8 noqa

from device import random_cron_delay  # nopep8 noqa
random_cron_delay(sys.argv[1:])

LOG_CONFIG = "/etc/pproxy/logging.ini"
logging.config.fileConfig(LOG_CONFIG, disable_existing_loggers=False)
logger = logging.getLogger("periodic-ports")

from shadow import Shadow  # nopep8 noqa
s = Shadow(logger)
s.forward_all()

from tor import Tor  # nopep8 noqa
t = Tor(logger)
t.forward_all()

from wireguard import Wireguard  # nopep8 noqa
w = Wireguard(logger)
w.forward_all()

# Check that the API is not externally exposed.
# If so, APIs should shut down
from ipw import IPW  # nopep8 noqa
import requests  # nopep8 noqa
ipw = IPW()
external_ip = str(ipw.myip())
status = WStatus(logger)
local_token = status.get_field('status', 'local_token')

# Phase 1: Probe the external IP without sending the sensitive local_token.
# If the port is open externally, the server will reply (likely 401/403 or 503 depending on token match,
# but critically, it will succeed to establish a TCP/TLS connection instead of timing out).
external_url = "https://" + external_ip + ":5000/api/v1/port_exposure/check"
logger.info("Phase 1: Probing external exposure without token: " + external_url)

port_exposed = False
try:
    # Probe without local_token. Set verify=False since it's a self-signed ad-hoc cert.
    r = requests.post(external_url, timeout=2, verify=False)  # nosec
    # A successful response status (even 401 Unauthorized or 403 Forbidden) indicates
    # the server is listening and reachable on the external interface!
    # If the port is closed or blocked, it will throw a connection/timeout exception.
    logger.info(f"Phase 1 response code: {r.status_code}. External port is REACHABLE.")
    port_exposed = True
except Exception:
    logger.info("OK: API port is not reachable externally (Connection failed/timed out).")

# Phase 2: If Phase 1 succeeded (reachable), securely notify the LAN API using the
# local loopback interface (127.0.0.1), passing the actual local_token safely.
if port_exposed:
    logger.warning("External exposure detected! Triggering secure shutdown via loopback.")
    local_url = "https://127.0.0.1:5000/api/v1/port_exposure/check"
    try:
        r_local = requests.post(
            local_url,
            data={'local_token': str(local_token)},
            timeout=2,
            verify=False  # nosec: local loopback cert
        )
        logger.info(f"Exposure shutdown response: {r_local.text}")
    except Exception as ex:
        logger.error(f"Failed to trigger local exposure check: {ex}")
