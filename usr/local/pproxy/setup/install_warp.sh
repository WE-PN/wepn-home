w="warp-cli --accept-tos"
reproxy_port=8999
warp_port=8971

curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list

sudo apt update 
apt install cloudflare-warp redsocks

$w registration new 
$w  mode proxy
$w  connect

$w disconnect
$w mode proxy
$w proxy port $warp_port
$w connect

mv /etc/redsocks.conf /etc/redsocks.conf.orig
cat > /etc/redsocks.conf <<EOF
base {
    log_debug = off;
    log_info = off;
    log = "stderr"; // Or a file like /var/log/redsocks.log
    daemon = on;    // Run as a daemon
    redirector = iptables;
}

redsocks {
    local_ip = 127.0.0.1;
    local_port = $reproxy_port; // redsocks will listen on this port for HTTP/S traffic

    ip = 127.0.0.1;
    port = $warp_port;       // WARP's SOCKS5 proxy IP and port

    type = socks5;
    // For HTTPS, redsocks typically handles CONNECT requests which SOCKS5 supports
}
EOF
sudo systemctl start redsocks
sudo systemctl enable redsocks # To start on boot
