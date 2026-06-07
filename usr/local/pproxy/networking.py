from device import Device
from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class Networking(Service):
    def __init__(self, logger):
        Service.__init__(self, "networking", logger)
        return

    def start(self):
        device = Device(self.logger)
        device.execute_setuid("1 8", detached=True)
        device.execute_setuid("1 9", detached=True)
        return

    def configure(self, str_conf):
        prev_uplink = self.service_config.get_field(self.name, 'uplink')
        prev_mode = self.service_config.get_field(self.name, 'routing-mode')
        super().configure(str_conf)
        new_uplink = self.service_config.get_field(self.name, 'uplink')
        new_mode = self.service_config.get_field(self.name, 'routing-mode')
        if new_uplink != prev_uplink or new_mode != prev_mode:
            self.logger.info(f"networking config changed ({prev_uplink}/{prev_mode} -> {new_uplink}/{new_mode}), reapplying rules")
            self.start()
