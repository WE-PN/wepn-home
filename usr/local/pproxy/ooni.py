from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class OONI(Service):
    def __init__(self, logger):
        Service.__init__(self, "ooni", logger)
        return

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "probeEnabled": self.is_enabled(),
                'reportCollectorEnabled': False,
                'reportCollectorPort': 0
            },
        }
        return settings_json
