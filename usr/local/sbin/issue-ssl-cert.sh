#!/bin/bash
# Issues/renews a Let's Encrypt cert via certbot DNS-01 (Cloudflare) and deploys
# it to the local API server. Invoked exclusively via wepn-run as root.

CONFIG_FILE="/etc/pproxy/config.ini"
LOCAL_CERT="/usr/local/pproxy/local_server/wepn-local.crt"
LOCAL_KEY="/usr/local/pproxy/local_server/wepn-local.key"

# Read and sanitize config values in Python (strip any char outside the
# allowed set so a crafted config.ini cannot inject shell commands).
read_config() {
    python3 - <<'PYEOF'
import configparser, re, sys

def sanitize(val, pattern):
    return re.sub(pattern, '', val)

c = configparser.ConfigParser()
c.read('/etc/pproxy/config.ini')

enabled   = c.getboolean('dyndns', 'enabled', fallback=False)
method    = sanitize(c.get('dyndns', 'method',           fallback=''), r'[^A-Za-z0-9_-]')
issue_ssl = c.getboolean('dyndns', 'issue_certbot_ssl',  fallback=False)
token     = sanitize(c.get('dyndns', 'token',            fallback=''), r'[^A-Za-z0-9_.=+-]')
hostname  = sanitize(c.get('dyndns', 'hostname',         fallback=''), r'[^A-Za-z0-9.-]')

def safe_port(val, default):
    try:
        p = int(val)
        return p if 1 <= p <= 65535 else default
    except (ValueError, TypeError):
        return default

port_ssl    = safe_port(c.get('haproxy', 'port_ssl',    fallback='443'),  443)
port_a      = safe_port(c.get('haproxy', 'port_a',      fallback='5222'), 5222)
port_b      = safe_port(c.get('haproxy', 'port_b',      fallback='4244'), 4244)
port_signal = safe_port(c.get('haproxy', 'port_signal', fallback='6443'), 6443)

print(int(enabled))
print(method)
print(int(issue_ssl))
print(token)
print(hostname)
print(port_ssl)
print(port_a)
print(port_b)
print(port_signal)
PYEOF
}

mapfile -t cfg < <(read_config)
ENABLED="${cfg[0]}"
METHOD="${cfg[1]}"
ISSUE_SSL="${cfg[2]}"
TOKEN="${cfg[3]}"
HOSTNAME="${cfg[4]}"
PORT_SSL="${cfg[5]}"
PORT_A="${cfg[6]}"
PORT_B="${cfg[7]}"
PORT_SIGNAL="${cfg[8]}"

if [ "$ENABLED" != "1" ] || [ "$METHOD" != "cloudflare" ] || [ "$ISSUE_SSL" != "1" ]; then
    exit 0
fi

# Validate format before any use — defence in depth after Python sanitization.
if ! [[ "$TOKEN" =~ ^[A-Za-z0-9_.=+-]{20,512}$ ]]; then
    logger -t ssl-cert "invalid or missing token in config"
    exit 1
fi
if ! [[ "$HOSTNAME" =~ ^[A-Za-z0-9.-]{3,253}$ ]]; then
    logger -t ssl-cert "invalid or missing hostname in config"
    exit 1
fi

CREDS_FILE=$(mktemp --suffix=.ini)
chmod 600 "$CREDS_FILE"
trap 'rm -f "$CREDS_FILE"' EXIT

printf 'dns_cloudflare_api_token = %s\n' "$TOKEN" > "$CREDS_FILE"

CERTBOT_OUTPUT=$(certbot certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials "$CREDS_FILE" \
    -d "$HOSTNAME" \
    --non-interactive \
    --agree-tos \
    --email "support@we-pn.com" \
    --keep-until-expiring 2>&1)

CERTBOT_RC=$?
if [ $CERTBOT_RC -ne 0 ]; then
    logger -t ssl-cert "certbot failed (rc=$CERTBOT_RC): $CERTBOT_OUTPUT"
    exit 1
fi

LIVE_CERT="/etc/letsencrypt/live/${HOSTNAME}/fullchain.pem"
LIVE_KEY="/etc/letsencrypt/live/${HOSTNAME}/privkey.pem"

if [ ! -f "$LIVE_CERT" ] || [ ! -f "$LIVE_KEY" ]; then
    logger -t ssl-cert "cert files not found at /etc/letsencrypt/live/${HOSTNAME}/"
    exit 1
fi

HAPROXY_DIR="/var/local/pproxy/haproxy"
mkdir -p "$HAPROXY_DIR"

cat "$LIVE_CERT" "$LIVE_KEY" > "${HAPROXY_DIR}/haproxy.pem"
chmod 600 "${HAPROXY_DIR}/haproxy.pem"

cat > "${HAPROXY_DIR}/haproxy.cfg" <<HACFG
resolvers dns
    nameserver google 8.8.8.8:53
    nameserver cloudflare 1.1.1.1:53
    hold valid 30s
    hold other 10s

global
    maxconn 4096
    user haproxy
    group haproxy
    log /dev/log local0

defaults
    mode tcp
    log global
    option tcplog
    timeout connect 5s
    timeout client 50s
    timeout server 50s

frontend ssl
    bind *:${PORT_SSL} ssl crt ${HAPROXY_DIR}/haproxy.pem
    default_backend ssl

backend ssl
    server s1 g.whatsapp.net:443 ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt resolvers dns resolve-prefer ipv4

frontend tcp_a
    bind *:${PORT_A} ssl crt ${HAPROXY_DIR}/haproxy.pem
    default_backend tcp_a

backend tcp_a
    server s1 g.whatsapp.net:5222 resolvers dns resolve-prefer ipv4

frontend tcp_b
    bind *:${PORT_B} ssl crt ${HAPROXY_DIR}/haproxy.pem
    default_backend tcp_b

backend tcp_b
    server s1 g.whatsapp.net:443 ssl verify required ca-file /etc/ssl/certs/ca-certificates.crt resolvers dns resolve-prefer ipv4

frontend signal
    bind *:${PORT_SIGNAL} ssl crt ${HAPROXY_DIR}/haproxy.pem
    default_backend signal

backend signal
    server s1 chat.signal.org:443 resolvers dns resolve-prefer ipv4
HACFG

if cmp -s "$LIVE_CERT" "$LOCAL_CERT"; then
    systemctl is-active --quiet wepn-haproxy && systemctl reload-or-restart wepn-haproxy || true
    exit 0
fi

cp "$LIVE_CERT" "$LOCAL_CERT"
cp "$LIVE_KEY" "$LOCAL_KEY"
chown wepn-api:wepn-web "$LOCAL_CERT" "$LOCAL_KEY"
chmod 660 "$LOCAL_CERT" "$LOCAL_KEY"

systemctl restart wepn-api
logger -t ssl-cert "deployed cert for ${HOSTNAME} and restarted wepn-api"
systemctl is-active --quiet wepn-haproxy && systemctl reload-or-restart wepn-haproxy || true
logger -t ssl-cert "updated haproxy cert"
