import logging
import requests
import re


class IPW():
    def __init__(self):
        self.logger = logging.getLogger("ipw")

    def myip(self):
        # get the ip.we-pn.com IP
        try:
            f = requests.get('https://ip.we-pn.com', timeout=10)
            ip = str(f.text).rstrip()
            # check if it is valid, not an error message
            regex = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
            if re.search(regex, ip):
                return ip
            else:
                self.logger.warning("Not a valid ipv4 address")
                return "127.0.0.1"
        except OSError:
            self.logger.error("Error in connection to IP resolver service")
            pass
        return 0
