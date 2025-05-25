import json
try:
    from configparser import configparser
except ImportError:
    import configparser
from wstatus import WStatus

CONFIG_FILE = '/etc/pproxy/config.ini'
SERVICE_FILE_BASE = "/var/local/pproxy/"
# setuid command runner
SRUN = "/usr/local/sbin/wepn-run"


class Service:
    def __init__(self, name, logger):
        self.name = name
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.wstatus = WStatus(logger)
        path = SERVICE_FILE_BASE + "/" + name + ".ini"
        self.service_config = WStatus(logger, source_file=path)
        self.logger = logger
        return

    def add_user(self, certname, ip_address, password, port, lang):
        return False

    def delete_user(self, certname):
        return

    def start(self):
        return

    def stop(self):
        return

    def restart(self):
        self.stop_all()
        self.start_all()

    def reload(self):
        pass

    def get_config_section_name(self):
        if self.name == "shadowsocks":
            service_config_name = "shadow"
        else:
            service_config_name = self.name
        return service_config_name

    def is_enabled(self):
        # TODO: this is a workaround until we update service name everywhere
        service_config_name = self.get_config_section_name()
        if self.config.has_section(service_config_name):
            service_present = (int(self.config.get(service_config_name, 'enabled')) == 1)
            service_active = self.service_config.get_service_status(self.name)
            return service_present and service_active
        else:
            return False

    def set_enabled(self, is_enabled, save=True):
        previously_enabled = self.is_enabled()
        self.service_config.set_service_status(self.name, is_enabled)
        if save:
            self.service_config.save()
        if is_enabled and not previously_enabled:
            self.start()
        if not is_enabled and previously_enabled:
            self.stop()

    def can_email(self):
        if self.config.has_section(self.get_config_section_name()):
            return (int(self.config.get(self.get_config_section_name(), 'email')) == 1)
        else:
            return False

    def get_service_creds_summary(self, ip_address):
        return {}

    def get_usage_status_summary(self):
        return {}

    def get_usage_daily(self):
        return {}

    def get_short_link_text(self, cname, ip_address):
        return ""

    def get_add_email_text(self, certname, ip_address, lang, is_new_user=False):
        txt = ''
        html = ''
        subject = ''
        attachments = []
        return txt, html, attachments, subject

    def get_removal_email_text(self, certname, ip_address):
        txt = ''
        html = ''
        subject = ''
        attachments = []
        return txt, html, attachments, subject

    def get_access_link(self, cname):
        return None

    def execute_setuid(self, cmd):
        return self.execute_cmd(SRUN + " " + cmd)

    def execute_cmd(self, cmd):
        pass

    def recover_missing_servers(self):
        return

    def self_test(self):
        return True

    def configure(self, config_data):
        self.logger.error("configuring with : " + str(config_data))
        if "enabled" in config_data:
            self.set_enabled(config_data["enabled"])

    def backup_restore(self):
        return True

    def get_config_settings(self):
        settings_json = {
            "name": self.name,
            "settings": {
                "enabled": False,
            },
        }
        return settings_json

    def apply_config_settings(self, str_conf):
        json_conf = json.loads(str_conf)
        self.set_enabled(json_conf["settings"]["enabled"])
        return

    def get_overlayable_config_value(self, field_name, default=None):
        value = None
        try:
            overlay_value = None
            if self.config.has_option(
                    self.get_config_section_name(),
                    field_name):
                value = self.config.get(self.get_config_section_name(),
                                        field_name)
            if self.service_config.has_option(
                    self.name,
                    field_name):
                overlay_value = self.service_config.get_field(self.name, field_name)
            if overlay_value is not None and overlay_value != "":
                value = overlay_value
        except:
            self.logger.exception(f"Could not get value for {field_name}")
        if value is None:
            value = default
        return value

    def get_service_config_file(self):
        return SERVICE_FILE_BASE + "/" + self.name + ".ini"
