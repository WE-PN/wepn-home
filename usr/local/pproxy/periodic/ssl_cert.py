import getopt
import logging
import random
import subprocess  # nosec: static input, go.we-pn.com/waiver-1
import sys
import time
import configparser

CONFIG_FILE = "/etc/pproxy/config.ini"
WEPN_RUN = "/usr/local/sbin/wepn-run"
SSL_CERT_CMD_INDEX = "30"

logger = logging.getLogger("ssl-cert")


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


def _random_cron_delay(args):
    try:
        opts, _ = getopt.getopt(args, "d", ["random-delay"])
        if any(o in ("-d", "--random-delay") for o, _ in opts):
            time.sleep(random.randint(20, 300))  # nosec: not used for cryptography
    except getopt.GetoptError:
        pass


def main(config):
    if _is_configured(config):
        result = subprocess.run([WEPN_RUN, "1", SSL_CERT_CMD_INDEX])  # nosec: static input, go.we-pn.com/waiver-1
        if result.returncode != 0:
            logger.error("ssl-cert: wepn-run returned rc=%d", result.returncode)


if __name__ == "__main__":
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    if _is_configured(cfg):
        _random_cron_delay(sys.argv[1:])
        main(cfg)
