import atexit
import json

from device import Device
from device import SRUN as SRUN
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
        # add M-F, 1-3AM PST by dfault, to limit bandwidth even further
        # this will be removed later once we have input from users via app
        for day in range(5):
            self.add_scheduled_time(day, [1, 2, 3])

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
        self.apply_bandwidth_limit()
        # start the wepn-unbounded service
        device.execute_setuid("0 6 1")

    def stop_all(self):
        device = Device(self.logger)
        device.execute_setuid("0 6 0")

    def start(self):
        self.start_all()
        return

    def stop(self):
        self.stop_all()
        return

    def restart(self):
        self.stop_all()
        self.start_all()

    def reload(self):
        return

    def apply_bandwidth_limit(self):
        device = Device(self.logger)
        # applying the limit will clear old rules, which will reset counters
        # so we apply them only if rule is not already present
        # TODO: cover case where the limit is changed
        if not self.is_limit_rule_present(device):
            device.execute_setuid(f"1 23 set unbounded eth0 {self.get_limit()}")

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
        self.service_config.save()
        self.self_test()
        self.recover_missing_servers()
        return

    def get_usage_status_summary(self):
        return {"unbounded": 1}

    def clear_usage_counters(self, device=None):
        if device is None:
            device = Device(self.logger)
        iface = str(self.config.get('hw', 'iface'))
        cmd = " 1 25 unbounded " + iface
        result, err, failed, sp = device.execute_cmd_output(SRUN + cmd)
        return err

    def is_limit_rule_present(self, device=None):
        return (self.get_usage_bits(device) != -1)

    def get_usage_bits(self, device=None):
        if device is None:
            device = Device(self.logger)
        iface = str(self.config.get('hw', 'iface'))
        cmd = " 1 24 unbounded " + iface
        try:
            result, err, failed, sp = device.execute_cmd_output(SRUN + cmd)
            bits = int(result.decode("utf-8").strip()) * 8
        except:
            bits = -1
        return bits

    def get_usage_deltas(self, long_term=False, clear_counters=False):
        device = Device(self.logger)
        bits = max(self.get_usage_bits(device), 0)
        if clear_counters:
            self.clear_usage_counters(device)
        return {"unbounded": bits}
