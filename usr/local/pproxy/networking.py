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
