files=""
covs=""
for i in `ls tests/units/*.py`; do 
	files+="$i " 
	covs+="--cov=`echo $i | awk -F_ '{print $2}' | awk -F. '{print $1}'` "
done

echo $files
echo $covs

echo pytest -v --cov-report term-missing $files $covs
#pytest -v --cov-report term-missing $files $covs
