GEO_DIR=/var/local/pproxy/geolocation
if [ ! -f "$GEO_DIR/asn.mmdb" ]; then
	mkdir $GEO_DIR
	wget https://wepn.dev/geolocation.tar.gz  -O $GEO_DIR/geolocation.tar.gz
	tar -xvzf $GEO_DIR/geolocation.tar.gz -C /var/local/pproxy/
	rm $GEO_DIR/geolocation.tar.gz
fi
