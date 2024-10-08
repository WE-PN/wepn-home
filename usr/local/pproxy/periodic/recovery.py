import os
import logging
import logging.config
import requests
import sys
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from shadow import Shadow  # nopep8
from device import Device  # nopep8


LOG_CONFIG = "/etc/pproxy/logging-debug.ini"
logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)

logger = logging.getLogger("recovery")
device = Device(logger)
shadow_server = Shadow(logger)
# each service should back up and restore their critical services
# currently only shadowsocks has a crtical DB
# TODO: replace with services.rercover_misisng_servers() and services.backup_restor()
# might make sense to create an umbrella method like services.heal() which calls these
shadow_server.backup_restore()
# now recover missing servers
shadow_server.recover_missing_servers()


# if local API server is down, restart it
url = "https://127.0.0.1:5000/"
try:
    r = requests.get(url, timeout=5, verify=False)  # nosec
    if (r.status_code != 200):
        logger.error("Some error in API")
        device.execute_setuid("1 15")
except:
    logger.error("Local server was down")
    # wepn-run 1 15
    device.execute_setuid("1 15")
