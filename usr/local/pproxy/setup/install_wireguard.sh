#######################################################################
# This script installs and configures existing open source packages
# providing WireGuard without any modification to WireGuard itself.
#
# WireGuard is not affiliated with WEPN, and has not endorsed nor
# sponsored WEPN. Similarly, WEPN is not affiliated with WireGuard,
# and does not sponsor or endorse their work.
#
# WireGuard is a registered trademarks of Jason A. Donenfeld.
# More information: http://www.wireguard.com
#######################################################################


# Until added as dependency to debian package
# or roll out package manager feature
# Next two lines should only run manually
# apt-get update && apt-get upgrade -y
# apt-get install wireguard

wg-quick down wg0
ORPORT=`cat /etc/pproxy/config.ini  | grep wireport | tr -d ' ' | awk -F"=" '{print $2}'`
PORT=${ORPORT:=6711}
PUBPUB=/var/local/pproxy/wireguard-publickey
echo "setting up wireguard on port $ORPORT"

configs_path=/var/local/pproxy/users/
mkdir -p $configs_path
chown pproxy:pproxy $configs_path

cd /etc/wireguard

umask 077
if [ -e wg0.conf ];
then
	server_priv=`cat /etc/wireguard/wg0.conf | grep PrivateKey | awk -F" = " '{print $2}'`
	echo $server_priv
	if [ ! -z $server_priv ]; then
		echo $server_priv > privatekey
		echo $server_priv | wg pubkey > publickey
	fi
fi

if [ ! -e privatekey ];
then
	wg genkey | tee privatekey | wg pubkey > publickey

fi

PUB_SERVER=`cat publickey`
PRIV_SERVER=`cat privatekey`

cat > /etc/wireguard/wg0.conf << EOF

[Interface]
PrivateKey = $PRIV_SERVER
Address = 10.93.76.1/24
ListenPort = $PORT
PostUp = iptables -I INPUT -p udp --dport $PORT -j ACCEPT
PostUp = iptables -I FORWARD -i eth0 -o wg0 -j ACCEPT
PostUp = iptables -I FORWARD -i wg0 -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostUp = ip6tables -I FORWARD -i wg0 -j ACCEPT
PostUp = ip6tables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D INPUT -p udp --dport $PORT -j ACCEPT
PostDown = iptables -D FORWARD -i eth0 -o wg0 -j ACCEPT
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
PostDown = ip6tables -D FORWARD -i wg0 -j ACCEPT
PostDown = ip6tables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

### Clients below
EOF

wg-quick up wg0
wg show
systemctl enable wg-quick@wg0

cp publickey $PUBPUB
chmod 0644 $PUBPUB
# just to be safe, umask should have already set it right
chmod 0600 privatekey

cd -
