#########################################
# Fix old setup files
# add missing fileds, correct host
#########################################
import configparser
import re
CONFIG_FILE = '/etc/pproxy/config.ini'
STATUS_FILE = '/var/local/pproxy/status.ini'
PORT_STATUS_FILE = '/var/local/pproxy/port.ini'

config = configparser.ConfigParser()
config.read(CONFIG_FILE)
status = configparser.ConfigParser()
status.read(STATUS_FILE)
port_status = configparser.ConfigParser()
port_status.read(PORT_STATUS_FILE)

if not config.has_section('mqtt'):
    config.add_section('mqtt')

if not config.has_option('mqtt', 'host'):
    config.set('mqtt', 'host', 'we-pn.com')
    config.set('mqtt', 'onboard-timeout', '10')

mqtt = config.get('mqtt', 'host')
if mqtt == "api.we-pn.com":
    config.set('mqtt', 'host', 'we-pn.com')

if not config.has_option('mqtt', 'onboard-timeout'):
    config.set('mqtt', 'onboard-timeout', '10')

if not config.has_section('django'):
    config.add_section('django')
    config.set('django', 'host', 'api.we-pn.com')
    config.set('django', 'url', 'https://api.we-pn.com')

host = config.get('django', 'host')
if host == "we-pn.com":
    config.set('django', 'host', 'api.we-pn.com')

url = config.get('django', 'url')
if url == "we-pn.com" or url == "https://we-pn.com":
    config.set('django', 'url', 'https://api.we-pn.com')

if not config.has_section('openvpn'):
    config.add_section('openvpn')
    config.set('openvpn', 'enabled', '0')
else:
    config.set('openvpn', 'enabled', '0')


if not config.has_option('openvpn', 'enabled'):
    config.set('openvpn', 'enabled', '0')
    config.set('openvpn', 'email', '0')

if not config.has_section('shadow'):
    config.add_section('shadow')
    config.set('shadow', 'enabled', '1')
    config.set('shadow', 'email', '1')
    config.set('shadow', 'conf_dir', '/var/local/pproxy/shadow/')
    config.set('shadow', 'conf_json', '/var/local/pproxy/shadow.json')
    config.set('shadow', 'db-path', '/var/local/pproxy/shadow.db')
    config.set('shadow', 'server-socket', '/var/local/pproxy/shadow/shadow.sock')
    config.set('shadow', 'method', 'aes-256-gcm')
    config.set('shadow', 'start-port', '4000')

if not config.has_section('usage'):
    config.add_section('usage')
    config.set('usage', 'db-path', '/var/local/pproxy/usage.db')


if not status.has_section('status'):
    status.add_section('status')
    status.set('status', 'claimed', '0')
    status.set('status', 'state', '1')
    status.set('status', 'mqtt', '0')
    status.set('status', 'pin', '00000000')
    status.set('status', 'mqtt-reason', '0')
    status.set('status', 'local_token', '0')
    status.set('status', 'temporary_key', '0')
    status.set('status', 'last_heartbeat_timestamp', '1')
else:
    if not status.has_option('status', 'last_heartbeat_timestamp'):
        status.set('status', 'last_heartbeat_timestamp', '1')

if not status.has_section('port_check'):
    status.add_section('port_check')
    status.set('port_check', 'last_check', '1985-10-26 01:21:00.680749')
    status.set('port_check', 'pending', 'False')
    status.set('port_check', 'experiment_number', '0')
    status.set('port_check', 'result', 'False')


if not port_status.has_section('port-fwd'):
    port_status.add_section('port-fwd')
    port_status.set('port-fwd', 'fails', '0')
    port_status.set('port-fwd', 'fails-max', '3')
    port_status.set('port-fwd', 'skipping', '0')
    port_status.set('port-fwd', 'skips', '0')
    port_status.set('port-fwd', 'skips-max', '20')

if not port_status.has_option('port-fwd', 'skipping-date'):
    port_status.set('port-fwd', 'skipping-date', '1985-10-26 01:21:00.680749')

if status.has_section('port-fwd'):
    status.remove_section('port-fwd')

if not config.has_section('email'):
    config.add_section('email')

# temporary re-update for an earlier bug
if config.has_option('email', 'enabled'):
    if config.get('email', 'enabled') == 'text':
        config.set('email', 'enabled', '1')

if not config.has_option('email', 'enabled'):
    config.set('email', 'enabled', '1')

if not config.has_option('email', 'type'):
    config.set('email', 'type', 'text')


config.set('email', 'email', "WEPN Device<devices@we-pn.com>")


if not status.has_section('previous_keys'):
    status.add_section('previous_keys')

if not status.has_option('status', 'last_diag_code'):
    status.set('status', 'last_diag_code', "127")

if not config.has_section('hw'):
    config.add_section('hw')

if not config.has_option('hw', 'iface'):
    config.set('hw', 'iface', 'eth0')

if (not config.has_option('hw', 'led-version') and
        not config.has_option('hw', 'lcd-version')):
    config.set('hw', 'led-version', '1')

if not config.has_option('hw', 'num_leds'):
    config.set('hw', 'num_leds', '27')

if config.has_option('hw', 'led-version'):
    v = config.get('hw', 'led-version')
    config.set('hw', 'lcd-version', v)
    config.remove_option('hw', 'led-version')

if config.has_option('hw', 'led'):
    v = config.get('hw', 'led')
    config.set('hw', 'lcd', v)
    config.remove_option('hw', 'led')

if not config.has_option('hw', 'button-version'):
    config.set('hw', 'button-version', '1')

if not config.has_option('hw', 'disable-reboot'):
    config.set('hw', 'disable-reboot', '0')

if not config.has_section('usage'):
    config.add_section('usage')
    config.set('usage', 'db-path', "/var/local/pproxy/usage.db")


if not config.has_section('dyndns'):
    config.add_section('dyndns')
    config.set('dyndns', 'enabled', "0")
    config.set('dyndns', 'username', "")
    config.set('dyndns', 'password', "")
    config.set('dyndns', 'hostname', "")
    config.set('dyndns', 'url', "https://{}:{}@domains.google.com/nic/update?hostname={}&myip={}")
    # config.set('dyndns','url', "http://{}:{}@dynupdate.no-ip.com/nic/update?hostname={}&myip={}")

if not config.has_option('dyndns', 'issue_certbot_ssl'):
    config.set('dyndns', 'issue_certbot_ssl', '0')

if not config.has_section('haproxy'):
    config.add_section('haproxy')
    config.set('haproxy', 'enabled', '0')
if not config.has_option('haproxy', 'port_ssl'):
    config.set('haproxy', 'port_ssl', '443')
if not config.has_option('haproxy', 'port_a'):
    config.set('haproxy', 'port_a', '5222')
if not config.has_option('haproxy', 'port_b'):
    config.set('haproxy', 'port_b', '4244')
if not config.has_option('haproxy', 'port_signal'):
    config.set('haproxy', 'port_signal', '6443')

# Tor installation and config
if not config.has_section('tor'):
    config.add_section('tor')
    config.set('tor', 'enabled', "1")
    config.set('tor', 'email', "1")
config.set('tor', 'db-path', "/var/local/pproxy/tor.db")
# forcing this to always be 9040, correcting previous error
config.set('tor', 'orport', "8991")
config.set('tor', 'transport', "9040")

if not status.has_option('status', 'e2e_key'):
    import secrets
    import base64
    t_key = secrets.token_bytes(16)
    rand_e2e_key = base64.urlsafe_b64encode(t_key).decode("utf-8").strip()
    status.set('status', 'e2e_key', str(rand_e2e_key))
    status.set('status', 'temp_e2e_key', str(rand_e2e_key))

# Wireguard installation and config
if not config.has_section('wireguard'):
    config.add_section('wireguard')
    config.set('wireguard', 'enabled', "1")
    config.set('wireguard', 'email', "1")
    config.set('wireguard', 'wireport', "6711")

# moving the software section
if config.has_section('software'):
    config.remove_section('software')

if not status.has_section('software'):
    status.add_section('software')
    status.set('software', 'channel', "prod")
else:
    if status.has_option('software', 'uplink'):
        status.remove_option('software', 'uplink')
    if status.has_option('software', 'routing-mode'):
        status.remove_option('software', 'routing-mode')
# June 2025: force all devices back to prod
status.set('software', 'channel', "prod")

# core networking info:
#   how to route, where to route, etc.
if not status.has_section('networking'):
    status.add_section('networking')
    status.set('networking', 'uplink', "tor")
    # all-traffic, geo
    status.set('networking', 'routing-mode', "geo")

# fallback defaults in config.ini so prevent_location_issue.sh has safe values
# when networking.ini is absent or empty (e.g. server sent empty config)
if not config.has_section('networking'):
    config.add_section('networking')
if not config.has_option('networking', 'uplink'):
    config.set('networking', 'uplink', 'tor')
if not config.has_option('networking', 'routing-mode'):
    config.set('networking', 'routing-mode', 'geo')

# WARP installation and config
if not config.has_section('warp'):
    config.add_section('warp')
    config.set('warp', 'enabled', "0")
    config.set('warp', 'redproxy-port', "8999")
else:
    if status.has_option('warp', 'proxy-port'):
        status.remove_option('warp', 'proxy-port')
    config.set('warp', 'warp-port', "8971")

# GCM is required, but older shadowsocks doesn't support it
config.set('shadow', 'method', 'aes-256-gcm')
status.set('status', 'sw', '1.20.9')

with open(CONFIG_FILE, 'w') as configfile:
    config.write(configfile)
with open(STATUS_FILE, 'w') as statusfile:
    status.write(statusfile)
with open(PORT_STATUS_FILE, 'w') as statusfile:
    port_status.write(statusfile)


def set_fan_temp(config_file, temp_millideg=80000, gpio_default=22):
    with open(config_file, 'r') as f:
        lines = f.readlines()
    fan_indices = [i for i, line in enumerate(lines)
                   if re.match(r'\s*dtoverlay=gpio-fan', line)]
    if not fan_indices:
        lines.append('dtoverlay=gpio-fan,gpiopin=%d,temp=%d\n' % (gpio_default, temp_millideg))
        changed = True
    else:
        m = re.search(r'gpiopin=(\d+)', lines[fan_indices[0]])
        gpiopin = int(m.group(1)) if m else gpio_default
        target = 'dtoverlay=gpio-fan,gpiopin=%d,temp=%d\n' % (gpiopin, temp_millideg)
        changed = (len(fan_indices) > 1 or lines[fan_indices[0]].strip() != target.strip())
        lines[fan_indices[0]] = target
        for i in reversed(fan_indices[1:]):
            lines.pop(i)
    if changed:
        with open(config_file, 'w') as f:
            f.writelines(lines)


try:
    set_fan_temp('/boot/firmware/config.txt')
except Exception as e:
    print('Warning: could not update fan config: ' + str(e))
