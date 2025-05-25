from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class WiFi(Service):
    def __init__(self, logger):
        Service.__init__(self, "wifi", logger)
        return

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "enabled": self.is_enabled(),
                "autoconnect": True,
            },
            "secure_settings": {
                "nonce": "",
                "data": "",
            },
        }
        return settings_json
