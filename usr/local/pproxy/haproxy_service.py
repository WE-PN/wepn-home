import configparser
import os
import socket

from constants import CONFIG_FILE
from device import Device
from service import Service

_HAPROXY_DIR = "/var/local/pproxy/haproxy"
_HAPROXY_PEM = _HAPROXY_DIR + "/haproxy.pem"


class HAProxyService(Service):
    def __init__(self, logger):
        Service.__init__(self, "haproxy", logger)
        self.system_service_name = "wepn-haproxy.service"

    def is_enabled(self):
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        return config.getboolean("haproxy", "enabled", fallback=False)

    def _cert_ready(self):
        return os.path.exists(_HAPROXY_PEM)

    def _get_ports(self):
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        port_ssl = config.getint("haproxy", "port_ssl", fallback=443)
        port_a = config.getint("haproxy", "port_a", fallback=5222)
        port_b = config.getint("haproxy", "port_b", fallback=4244)
        port_signal = config.getint("haproxy", "port_signal", fallback=6443)
        return port_ssl, port_a, port_b, port_signal

    def forward_ports(self):
        if not self.is_enabled() or not self._cert_ready():
            return
        port_ssl, port_a, port_b, port_signal = self._get_ports()
        device = Device(self.logger)
        device.open_port(port_ssl, "haproxy ssl")
        device.open_port(port_a, "haproxy tcp-a")
        device.open_port(port_b, "haproxy tcp-b")
        device.open_port(port_signal, "haproxy signal")

    def start_all(self):
        device = Device(self.logger)
        device.execute_setuid("0 7 1")

    def stop_all(self):
        device = Device(self.logger)
        device.execute_setuid("0 7 0")

    def start(self):
        if not self.is_enabled():
            return
        if not self._cert_ready():
            self.logger.warning("haproxy service: cert not ready at %s", _HAPROXY_PEM)
            return
        self.start_all()

    def stop(self):
        if self.is_running():
            self.stop_all()

    def _probe_port(self, port, timeout=5):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
                s.settimeout(timeout)
                try:
                    data = s.recv(128)
                    self.logger.info("haproxy self-test port %d: ok, %d bytes from backend", port, len(data))
                except socket.timeout:
                    self.logger.info("haproxy self-test port %d: connected, backend awaiting client data", port)
                return True
        except Exception as exc:
            self.logger.warning("haproxy self-test port %d: %s", port, exc)
            return False

    def self_test(self):
        if not self.is_enabled() or not self.is_running():
            return True
        port_ssl, port_a, port_b, port_signal = self._get_ports()
        results = [self._probe_port(p) for p in (port_ssl, port_a, port_b, port_signal)]
        return all(results)

    def get_config_settings(self):
        return {
            "name": self.name,
            "settings": {"enabled": self.is_enabled()},
            "secure_settings": {"nonce": "", "data": ""}
        }
