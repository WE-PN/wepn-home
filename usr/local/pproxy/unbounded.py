import atexit
import json

from device import Device
from ipw import IPW
from service import Service

ipw = IPW()

'''
This controls Unbounded by Lantern: https://unbounded.lantern.io/

Unbounded allows your browser, or in this case your pod, to become and entry brdige to Lantern's network.
Their traffic will not exit this pod, and will exit servers owned by Lantern.
'''


class Unbounded(Service):
    def __init__(self, logger):
        super().__init__("unbounded", logger)
        atexit.register(self.cleanup)
        self.system_service_name = "wepn-unbounded.service"

    def get_limit(self):
        return self.get_overlayable_config_value("bw-limit", "28kbit")

    def is_enabled(self):
        is_claimed = (int(self.wstatus.get('claimed')) == 1)
        is_active = self.get_overlayable_config_value('enabled', False)
        return (is_claimed and is_active)

    def is_kindness_mode(self):
        return True

    def cleanup(self):
        self.clear()

    def clear(self):
        pass

    def start_all(self):
        device = Device(self.logger)
        device.execute_setuid("0 6 1")

    def stop_all(self):
        device = Device(self.logger)
        device.execute_setuid("0 6 0")

    def start(self):
        device = Device(self.logger)
        self.start_all()
        # TODO: set hour limits
        device.execute_setuid(f"1 23 set unbounded eth0 {self.get_limit()}")
        return

    def stop(self):
        device = Device(self.logger)
        self.stop_all()
        # TODO: remove hour limits
        device.execute_setuid(f"1 23 remove unbounded eth0 {self.get_limit()}")
        return

    def restart(self):
        self.stop_all()
        self.start_all()

    def reload(self):
        return

    def self_test(self):
        # TODO: some good testing is really needed here
        success = True
        return success

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "enabled": self.is_enabled(),
                "bw-limit": self.get_limit(),
            },
        }
        return settings_json

    def configure(self, json_conf):
        self.service_config.set_service_config(self.name, json.dumps(json_conf))
        self.self_test()
        self.recover_missing_servers()
        return
