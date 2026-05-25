import os
import sys
import logging
import subprocess  # nosec: static input, go.we-pn.com/waiver-1
import configparser

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)

from device import random_cron_delay
from constants import CONFIG_FILE

logger = logging.getLogger("ssl-cert")

WEPN_RUN = "/usr/local/sbin/wepn-run"
SSL_CERT_CMD_INDEX = "30"


def _is_configured(config):
    if not config.has_section("dyndns"):
        return False
    if not config.getboolean("dyndns", "enabled", fallback=False):
        return False
    if config.get("dyndns", "method", fallback="") != "cloudflare":
        return False
    if not config.getboolean("dyndns", "issue_certbot_ssl", fallback=False):
        return False
    if not config.get("dyndns", "token", fallback=""):
        logger.warning("issue_certbot_ssl=1 but token is missing")
        return False
    if not config.get("dyndns", "hostname", fallback=""):
        logger.warning("issue_certbot_ssl=1 but hostname is missing")
        return False
    return True


def main(config):
    if _is_configured(config):
        result = subprocess.run([WEPN_RUN, "1", SSL_CERT_CMD_INDEX])  # nosec: static input, go.we-pn.com/waiver-1
        if result.returncode != 0:
            logger.error("ssl-cert: wepn-run returned rc=%d", result.returncode)


if __name__ == "__main__":
    random_cron_delay(sys.argv[1:])
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    main(cfg)
