covs=""
for i in `ls *.py`; do 
	covs+="--cov=`echo $i | awk -F. '{print $1}'` "
done
#covs=" --cov=constants --cov=debug --cov=device --cov=diag --cov=echo --cov=heartbeat --cov=ipw --cov=lcd --cov=led_client --cov=messages --cov=metrics_client --cov=networking --cov=ooni --cov=openvpn --cov=pproxy --cov=run --cov=service --cov=services --cov=shadow --cov=ssh --cov=tor --cov=unbounded --cov=wifi --cov=wireguard --cov=wstatus"

pytest -v --cov-report term-missing tests/units/ $covs
#pytest -v --cov-report term-missing $files $covs
