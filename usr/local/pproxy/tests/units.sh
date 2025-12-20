for i in `ls tests/units/*.py`; do pytest $i --cov=`echo $i | awk -F_ '{print $2}' | awk -F. '{print $1}'` ; done
