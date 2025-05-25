from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class Networking(Service):
    def __init__(self, logger):
        Service.__init__(self, "networking", logger)
        return
