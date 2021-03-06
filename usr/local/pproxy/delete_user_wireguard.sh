#!/bin/bash

if [ $# -eq 0 ]
then
	echo "provide peer alias"
else
	pub=`cat users/$1/publickey`
	sudo wg set wg0 peer $pub remove
	sudo wg show
	rm -rf users/$1/
	wg-quick save wg0
	systemctl restart wg-quick@wg0
fi
