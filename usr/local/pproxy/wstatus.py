import json

try:
    from configparser import configparser
except ImportError:
    import configparser
STATUS_FILE = '/var/local/pproxy/status.ini'


class WStatus:
    def __init__(self, logger, source_file=None):
        self.status = configparser.ConfigParser()
        self.logger = logger
        if source_file is None:
            source_file = STATUS_FILE
        self.status.read(source_file)
        self.source_file = source_file

    def save(self):
        # TODO: add lock checking
        if self.source_file is not None and self.status is not None:
            self.logger.info(f"writing status file {self.source_file}")
            try:
                statusfile = open(self.source_file, 'w')
                self.status.write(statusfile)
                statusfile.close()
            except Exception as err:
                self.logger.debug(
                    "Something happened when writing status file:" + self.source_file
                    + " error:" + str(err))

    def reload(self):
        self.status.read(self.source_file)

    def has_section(self, section):
        return self.status.has_section(section)

    def has_option(self, section, option):
        if not self.status.has_section(section):
            return False
        return self.status.has_option(section, option)

    def add_section(self, section):
        return self.status.add_section(section)

    def set(self, field, value):
        try:
            self.set_field('status', field, value)
        except:
            print("Unknown field to write:" + field)

    def set_field(self, section, field, value):
        if not isinstance(value, str):
            value = str(value)
        value = value.replace('%', '%%')
        try:
            self.status[section][field] = value
            self.logger.debug('setting ' + field + ' to ' + value)
        except:
            self.logger.exception("could not write a field")

    def get(self, field):
        try:
            return self.get_field('status', field)
        except:
            self.logger.exception("Unknown field: " + field)
            return ""

    def get_field(self, section, field):
        try:
            res = ""
            ret = self.status.get(section, field)
            if "[" in ret and "]" in ret:
                res = ret.strip('][\'\"').split(', ')
            else:
                res = ret
            if isinstance(res, list):
                return res[0]
            elif isinstance(res, str):
                return res.replace('%%', '%')
            else:
                return ret
        except:
            self.logger.exception("Unknown section/field: "
                                  + section + ":" + field +
                                  " in " + self.source_file)
            self.reload()
            return ""

    def set_service_status(self, service_name, is_enabled):
        if not self.status.has_section(service_name):
            self.status.add_section(service_name)
        self.status.set(service_name, 'enabled', str(is_enabled))

    def get_service_status(self, service_name):
        if not self.status.has_section(service_name):
            # unless a service is overriden, it is enabled
            return True
        return self.status.getboolean(service_name, 'enabled')

    def set_service_config(self, service_name, config_str):
        values = {}
        if not self.status.has_section(service_name):
            self.status.add_section(service_name)
        if isinstance(config_str, dict):
            values = config_str
        elif isinstance(config_str, str):
            try:
                values = json.loads(config_str)
            except:
                self.logger.exception("Could not load string")
                return ""

        for k, v in values.items():
            self.set_field(service_name, str(k), str(v))
