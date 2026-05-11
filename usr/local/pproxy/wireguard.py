from sanitize_filename import sanitize
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess  # nosec: sanitized with shlex, go.we-pn.com/waiver-1
import sys as system

from device import Device
from service import Service
from usage import Usage

CONFIG_FILE = '/etc/pproxy/config.ini'
USERS_DIR = "/var/local/pproxy/users/"
# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class Wireguard(Service):
    def __init__(self, logger):
        Service.__init__(self, "wireguard", logger)
        self.system_server_name = "wg-quick@wg0"
        self.usage = Usage(logger, service_type="wireguard", config=self.config)
        return

    def santizie_service_filename(self, filename):
        s = sanitize(filename)
        s = re.sub(r'[^a-zA-Z0-9\.]', '', s)
        s = s.lower()
        return s

    def add_user(self, certname, ip_address, password, port, lang):
        try:
            if not os.path.isdir(USERS_DIR):
                os.mkdir(USERS_DIR)
            if self.is_user_registered(certname):
                old_ip, old_port = self.get_external_ip_port_in_conf(certname)
                if old_ip == ip_address and old_port == int(port):
                    # nothing has changed, do nothing
                    return False
            cmd = '/bin/bash ./add_user_wireguard.sh '
            cmd += self.santizie_service_filename(certname)
            cmd += " " + str(self.get_port())
            self.logger.debug(cmd)
            self.execute_cmd(cmd)
            return self.is_user_registered(certname)
        except Exception as e:
            self.logger.exception(e)
            return False

    def delete_user(self, certname):
        cmd = '/bin/bash ./delete_user_wireguard.sh '
        cmd += self.santizie_service_filename(certname)
        self.logger.debug(cmd)
        self.execute_cmd(cmd)
        self.del_user_usage(certname)
        return

    def forward_all(self):
        port = int(self.get_port())
        device = Device(self.logger)
        device.open_port(port, "Wireguard")
        return

    def start(self):
        cmd = "1 22"
        self.logger.debug(cmd)
        self.execute_setuid(cmd)
        return

    def stop(self):
        cmd = "1 21"
        self.logger.debug(cmd)
        self.execute_setuid(cmd)
        return

    def restart(self):
        self.stop()
        self.start()
        return

    def reload(self):
        self.stop()
        self.start()
        return

    def get_users_list(self):
        users = []
        try:
            if os.path.isdir(USERS_DIR):
                for d in os.listdir(USERS_DIR):
                    if (os.path.isdir(USERS_DIR + d) and os.path.isfile(USERS_DIR + d + "/wg.conf")):
                        users.append(d)
            else:
                os.mkdir(USERS_DIR)
        except Exception as e:
            self.logger.exception(e)
        return users

    def get_service_creds_summary(self, ip_address):
        creds = {}
        for d in self.get_users_list():
            creds[d] = hashlib.sha256(self.get_short_link_text(d, ip_address).encode()).hexdigest()[:10]
        return creds

    def get_public_key_for_user(self, certname):
        cert_dir = self.santizie_service_filename(certname)
        pubkey_path = USERS_DIR + cert_dir + "/publickey"
        try:
            with open(pubkey_path, 'r') as f:
                return f.read().strip()
        except Exception:
            self.logger.exception(f"Could not read public key for {certname}")
            return None

    def get_current_reading(self):
        """Get per-certname transfer bytes from wg. Returns dict of certname -> rx+tx bytes."""
        pubkey_to_certname = {}
        for certname in self.get_users_list():
            pubkey = self.get_public_key_for_user(certname)
            if pubkey:
                pubkey_to_certname[pubkey] = certname
        if not pubkey_to_certname:
            return {}
        try:
            result = subprocess.run(  # nosec: fixed args, go.we-pn.com/waiver-1
                [SRUN, '1', '29'],
                capture_output=True,
                timeout=5
            )
            if result.returncode != 0:
                self.logger.error("wg show transfer failed: " + result.stderr.decode())
                return {}
            readings = {}
            for line in result.stdout.decode().splitlines():
                parts = line.split()
                if len(parts) == 3:
                    pubkey, rx, tx = parts
                    if pubkey in pubkey_to_certname:
                        readings[pubkey_to_certname[pubkey]] = int(rx) + int(tx)
            return readings
        except Exception:
            self.logger.exception("Could not get WireGuard transfer stats")
            return {}

    def get_usage_for_servers(self, periodic=False, clear_counters=False):
        users = self.get_users_list()
        if not users or not self.is_enabled():
            return {}, {}
        usage_deltas = {}
        usage_statuses = {}
        current_reading = self.get_current_reading()
        for certname in users:
            try:
                if certname in current_reading:
                    short_term_delta, long_term_delta, delta = self.usage.update_recorded_usage(
                        certname=certname,
                        new_reading=current_reading[certname],
                        clear_long_term=(clear_counters and periodic),
                        clear_short_term=(clear_counters and not periodic))
                    usage_statuses[certname] = 1 if delta > 0 else 0
                    usage_deltas[certname] = (long_term_delta if periodic else short_term_delta) * 8
                else:
                    self.logger.debug(f"No WireGuard reading for peer {certname}")
                    usage_statuses[certname] = -1
                    usage_deltas[certname] = -1
            except Exception as e:
                self.logger.error(f"Error getting WireGuard usage for {certname}: {e}")
                usage_statuses[certname] = -1
                usage_deltas[certname] = -1
        return usage_statuses, usage_deltas

    def get_usage_status_summary(self):
        users = self.get_users_list()
        usage = {}
        for certname in users:
            try:
                usage[certname] = 1 if self.usage.get_record_for_cert(certname)['short_term'] > 0 else 0
            except Exception:
                self.logger.exception(f"Error getting usage for WireGuard user {certname}")
                usage[certname] = -1
        return usage

    def get_usage_deltas(self, long_term=False, clear_counters=False):
        _, deltas = self.get_usage_for_servers(periodic=long_term, clear_counters=clear_counters)
        return deltas

    def del_user_usage(self, certname):
        return self.usage.del_user_usage(certname)

    def get_usage_daily(self):
        return {}

    def get_external_ip_port_in_conf(self, certname):
        config_file_path = self.get_user_config_file_path(certname)
        if config_file_path is None:
            return None, None

        with open(config_file_path, "r") as f:
            contents = f.read()
            endpoint_match = re.search(r"^Endpoint\s*=\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)\s*$", contents, re.MULTILINE)
        if endpoint_match:
            return endpoint_match.group(1), int(endpoint_match.group(2))
        else:
            return None, None

    def is_user_registered(self, certname):
        try:
            cert_dir = self.santizie_service_filename(certname)
            self.logger.debug("checking for user: " + cert_dir)
            return os.path.exists(USERS_DIR + cert_dir + "/wg.conf")
        except Exception:
            self.logger.exception("Could not check if wireguard registered")
            return False

    def get_user_config_file_path(self, certname):
        if self.is_user_registered(certname):
            cert_dir = self.santizie_service_filename(certname)
            return USERS_DIR + cert_dir + "/wg.conf"
        else:
            return None

    def get_short_link_text(self, cname, ip_address):
        encoded_string = ""
        filename = self.get_user_config_file_path(cname)
        if filename is not None:
            with open(filename, "rb") as file:
                encoded_string = "wg://" + str(base64.b64encode(file.read()).decode('utf-8'))
        return encoded_string

    def get_add_email_text(self, certname, ip_address, lang, is_new_user=False):
        txt = ''
        html = ''
        subject = "Your New VPN Access Details"
        attachments = []
        if self.is_enabled() and self.can_email() and self.is_user_registered(certname):
            txt = "To use Wireguard (" + ip_address + \
                  "): \n\n1. Download the attached certificate, \n2. Install Wireguard Client." + \
                  "\n3. Import the certificate you downloaded in the first step."
            html = "To use Wireguard (" + ip_address + \
                ")<ul><li>Download the attached certificate, \n <li>Install Wireguard Client." + \
                "<li> Import the certificate you downloaded in the first step.</ul>"
            attachments.append(self.get_user_config_file_path(certname))
        return txt, html, attachments, subject

    def get_access_link(self, cname):
        if self.is_user_registered(cname):
            try:
                conf = open(self.get_user_config_file_path(cname), "r").read().encode("utf-8")
                conf64 = base64.urlsafe_b64encode(conf)
                digest = hashlib.sha256(conf64).hexdigest()[:10]
                return "{\"type\":\"wireguard\", \"link\":\"" + \
                    "wg://" + conf64.decode('utf-8') + \
                    "\", \"digest\": \"" + str(digest) + "\"}"
            except Exception:
                self.logger.exception("Wireguard link crashing")
        return None

    def get_removal_email_text(self, certname, ip_address):
        txt = ''
        html = ''
        subject = ''
        attachments = []
        if self.config.get('wireguard', 'enabled') == 1 and self.config.get('wireguard', 'email') == 1:
            txt = "Access to VPN server IP address " + ip_address + " is revoked.",
            html = "Access to VPN server IP address " + ip_address + " is revoked.",

        return txt, html, attachments, subject

    def execute_setuid(self, cmd):
        return self.execute_cmd(SRUN + " " + cmd)

    def execute_cmd(self, cmd):
        self.logger.debug(cmd)
        try:
            args = shlex.split(cmd)
            process = subprocess.Popen(args)  # nosec: sanitized above, go.we-pn.com/waiver-1
            process.wait()
        except Exception as error_exception:
            self.logger.error(args)
            self.logger.error("Error happened in running command:" + cmd)
            self.logger.error("Error details:\n" + str(error_exception))
            system.exit()

    def recover_missing_servers(self):
        return

    def self_test(self):
        '''
        Perform a self-test of the Wireguard setup.
        '''
        # not implemented for Wireguard
        return True

    def get_enabled_peers(self):
        """
        Returns a list of currently enabled peers in the WireGuard setup.
        """
        enabled_peers = []
        for config_file in os.listdir(USERS_DIR):
            if os.path.isfile(os.path.join(USERS_DIR, config_file, "wg.conf")):
                with open(os.path.join(USERS_DIR, config_file, "wg.conf"), "r") as f:
                    contents = f.read()
                    if "[Peer]" in contents:
                        enabled_peers.append(config_file)
        return enabled_peers

    def get_port(self):
        return self.get_overlayable_config_value("wireport")

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "enabled": self.is_enabled(),
                "port": str(self.get_port()),
            },
        }
        return settings_json

    def configure(self, json_conf):
        self.service_config.set_service_config(self.name, json.dumps(json_conf))
        # not adding change_mode at this point, as it needs some validation
        self.set_enabled(json_conf["enabled"])
        return
