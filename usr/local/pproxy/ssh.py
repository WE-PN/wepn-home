import json

from service import Service
from device import Device

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class SSH(Service):
    def __init__(self, logger):
        Service.__init__(self, "ssh", logger)
        self.device = Device(logger)
        return

    def is_enabled(self):
        return self.device.is_ssh_service_running()

    def is_running(self):
        return self.is_enabled()

    def set_enabled(self, enabled):
        return self.device.set_sshd_service(enabled)

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "enabled": self.is_enabled(),
                "port": "22"
            },
            "secure_settings": {
                "nonce": "",
                "data": "",
            },
        }
        return settings_json

    def configure(self, json_conf):
        self.service_config.set_service_config(self.name, json.dumps(json_conf))
        self.set_enabled(json_conf["enabled"])
