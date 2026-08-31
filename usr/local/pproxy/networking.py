from device import Device
from service import Service

# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


# networking.ini fields that change the iptables routing state; a change to any
# of them triggers a rebuild via start().
ROUTING_FIELDS = ('uplink', 'routing-mode', 'block-quic')


class Networking(Service):
    def __init__(self, logger):
        Service.__init__(self, "networking", logger)
        return

    def start(self):
        device = Device(self.logger)
        # prevent_location_issue.sh (1 9) flushes and rebuilds the routing rules
        # atomically under its own flock. Do NOT also fire the bare flush (1 8):
        # the two are unordered when detached and the flush can land after the
        # rebuild, leaving the Pod on direct routing.
        device.execute_setuid("1 9", detached=True)
        return

    def _routing_field(self, field):
        # networking.ini is an overlay: a field the app has never set is simply
        # absent, which is normal here (not the "unknown field" case that
        # WStatus.get_field logs an error for). Treat missing as unset.
        if self.service_config.has_option(self.name, field):
            return self.service_config.get_field(self.name, field)
        return ""

    def configure(self, str_conf):
        self.service_config.reload()
        prev = [self._routing_field(f) for f in ROUTING_FIELDS]
        super().configure(str_conf)
        new = [self._routing_field(f) for f in ROUTING_FIELDS]
        if new != prev:
            self.logger.info(
                "networking config changed (%s -> %s), reapplying rules"
                % (dict(zip(ROUTING_FIELDS, prev)), dict(zip(ROUTING_FIELDS, new))))
            self.start()
