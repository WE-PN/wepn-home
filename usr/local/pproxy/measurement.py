from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class Measurement(Service):
    def __init__(self, logger):
        Service.__init__(self, "measurement", logger)
        return
