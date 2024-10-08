#!/usr/bin/python
import json
import random
import requests

from device import Device
from diag import WPDiag
from ipw import IPW
from lcd import LCD as LCD
from services import Services
from shadow import Shadow
from wstatus import WStatus

ipw = IPW()


# This can be updated whenever we add a new flag
# So other files should import and use this
# When trying to determine if device diag code is healthy
CONFIG_FILE = '/etc/pproxy/config.ini'
STATUS_FILE = '/var/local/pproxy/status.ini'
try:
    from self.configparser import ConfigParser as configparser
except ImportError:
    import configparser


class HeartBeat:
    def __init__(self, logger):
        # instantiate
        self.mqtt_connected = 0
        self.logger = logger
        self.mqtt_reason = 0
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.status = WStatus(logger)
        self.diag = WPDiag(logger)
        self.services = Services(logger)
        # This one is displayed on screen for call verification
        self.pin = random.SystemRandom().randint(1111111111, 9999999999)
        # This one is used for API access
        self.local_token = random.SystemRandom().randint(1111111111, 9999999999)
        # Print these to screen for bring-your-own device users without a screen
        self.logger.debug("PIN=" + str(self.pin))
        self.logger.debug("Local token=" + str(self.local_token))

    def is_connected(self):
        if self.diag:
            return self.diag.is_connected_to_internet()
        urls = [
            "http://connectivity.wepn.dev/",
            "http://connectivity.we-pn.com/",
            "https://status.we-pn.com",
            "https://twitter.com",
            "https://google.com",
            "https://www.speedtest.net/",
            "https://www.cnn.com/",
            "https://bbc.co.uk",
        ]
        random.shuffle(urls)

        for url in urls:
            try:
                # connect to the host -- tells us if the host is actually
                # reachable
                requests.get(url, timeout=10)
                return True
            except:
                self.logger.exception("Could not connect to the internet")
        return False

    def get_display_string_status(self, status, diag_code, lcd):
        if lcd.version > 1:
            icons, any_err, errs = lcd.get_status_icons_v2(status, diag_code)
            any_err = False
            if any_err:
                color = "red"
                display_str = [(1, str("   !"), 1, color),
                               (2, "         Error", 0, color),
                               (3, "", 0, color),
                               (4, "Help", 0, "white"), (5, "", 0, "black"),
                               (6, "Show QR Code", 0, "white"),
                               (7, icons, 1, "black")]
            else:
                color = "green"
                display_str = [(1, str("  0"), 1, color),
                               (2, "         OK", 0, color),
                               (3, "", 0, color),
                               (4, "", 0, "white"), (5, "", 0, "black"),
                               (6, "Menu", 0, "white"),
                               (7, "", 1, "black")]
        else:
            icons, any_err = lcd.get_status_icons(status,
                                                  self.is_connected(), self.mqtt_connected)
            if any_err:
                color = "red"
            else:
                color = "green"
            display_str = [(1, "PIN: ", 0, "blue"),
                           (2, str(self.pin), 0, "blue"),
                           (3, icons, 1, color)]

        return display_str

    def set_mqtt_state(self, is_connected, reason):
        self.mqtt_connected = is_connected
        self.mqtt_reason = reason
        pass

    # send heartbeat. if lcd_print==1, update LCD
    def send_heartbeat(self, lcd_print=0):
        headers = {"Content-Type": "application/json"}
        external_ip = str(ipw.myip())

        try:
            device = Device(self.logger)
            local_ip = device.get_local_ip()
        except Exception as e:
            print(e)
            local_ip = "127.0.0.1"
        test_port = int(self.config.get('openvpn', 'port')) + 10
        if int(self.config.get('shadow', 'enabled')) == 1:
            shadow = Shadow(self.logger)
            test_port = int(shadow.get_max_port()) + 12
        # this line can update the status file contents
        diag_code = self.diag.get_error_code(test_port)
        self.status.reload()

        # status 0 Service: Stopped RPI: Off
        # status 1 Service: Stopped RPI: Up
        # status 2 Service: Running RPI: Up
        # status 3 Service: Stopped RPI: Restarting
        # status 4 Service: Warming RPI: Up
        hb_left = int(self.status.get("hb_to_warm"))
        if hb_left > 0:
            # if device is still warming after cold boot or OOBE,
            # indicate that the app. otherwise, app will show
            # "error" warnings with incomplete data.
            status = 4
        else:
            status = int(self.status.get('state'))
        access_creds = self.services.get_service_creds_summary(external_ip)
        usage_status = self.services.get_usage_status_summary()
        self.logger.debug(usage_status)
        try:
            with open('local_server/wepn-local.sig') as f:
                # this signature is updated every time
                # a new local certificate is created
                signature = f.readline().strip()
        except OSError:
            signature = "NA"
        try:
            ports, num_forwards = device.get_all_port_mappings()
            logs = {"ports": ports, 'num_port_fwds': num_forwards}
        except:
            logs = "{'error':'Could not get port mappings'}"
        data = {
            "serial_number": self.config.get('django', 'serial_number'),
            "ip_address": external_ip,
            "status": str(status),
            "pin": str(self.pin),
            "local_token": str(self.local_token),
            "local_ip_address": str(local_ip),
            "device_key": self.config.get('django', 'device_key'),
            'port': self.config.get('shadow', 'start-port'),
            "software_version": self.status.get('sw'),
            "diag_code": diag_code,
            "access_cred": access_creds,
            "usage_status": usage_status,
            "public_key": signature,
            "log": logs,
        }
        self.status.set('pin', str(self.pin))
        prev_token = self.status.get('local_token')
        self.status.set('prev_token', str(prev_token))
        self.status.set('local_token', str(self.local_token))
        self.status.set('last_diag_code', str(diag_code))
        self.status.save()

        data_json = json.dumps(data)
        self.logger.debug("HB data to send: " + data_json)
        url = self.config.get('django', 'url') + "/api/device/heartbeat/"
        try:
            response = requests.get(url, data=data_json, headers=headers, timeout=10)
            self.logger.debug("Response to HB" + str(response.status_code))
        except requests.exceptions.RequestException as exception_error:
            self.logger.error(
                "Error in sending heartbeat: \r\n\t" + str(exception_error))
        if (lcd_print):
            lcd = LCD()
            lcd.set_lcd_present(self.config.get('hw', 'lcd'))
            display_str = self.get_display_string_status(status, diag_code, lcd)
            lcd.display(display_str, 20)

    def record_hb_send(self):
        left = self.status.get("hb_to_warm")
        if left == "":
            self.status.set("hb_to_warm", 3)
            left = 3
        left = int(left)
        if left > 0:
            self.status.set("hb_to_warm", str(left - 1))
        self.logger.debug("HB left = " + str(left))
        self.status.save()
