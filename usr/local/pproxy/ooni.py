from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class OONI(Service):
    def __init__(self, logger):
        Service.__init__(self, "ooni", logger)
        return
