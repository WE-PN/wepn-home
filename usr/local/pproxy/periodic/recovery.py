import os
import logging
import logging.config
import requests
import sys
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from device import Device  # nopep8
from services import Services  # nopep8
from constants import LOG_CONFIG as LOG_CONFIG

logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)

logger = logging.getLogger("recovery")
device = Device(logger)

####
# actions to apply to all services
####
services = Services(logger)
services.backup_restore()
services.recover_missing_servers()
services.apply_limits()

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
