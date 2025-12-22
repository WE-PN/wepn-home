covs=""
for i in `ls *.py`; do 
	covs+="--cov=`echo $i | awk -F. '{print $1}'` "
done

pytest -v --cov-report term-missing tests/units/ $covs
#pytest -v --cov-report term-missing $files $covs
