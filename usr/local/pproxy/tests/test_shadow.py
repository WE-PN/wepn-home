import logging.config
import os
import sys
from datetime import datetime
import pprint

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
# above line is needed for following classes:
from shadow import Shadow  # noqa E402 need up_dir first

LOG_CONFIG = "/etc/pproxy/logging-debug.ini"
logging.config.fileConfig(LOG_CONFIG, disable_existing_loggers=False)
logger = logging.getLogger("heartbeat")


sh = Shadow(logger)
r = sh.get_usage_json()
print(r)
print("is enabled? " + str(sh.is_enabled()))
today = datetime.today().strftime('%Y-%m-%d')
#pprint.pprint(sh.get_usage_daily(day_date=today))
pprint.pprint(sh.get_usage_daily())
pprint.pprint(sh.get_usage_status_summary())
print(sh.get_start_port())
#usage_results = sh.get_usage_status_summary()
#print(usage_results)
#access_creds = sh.get_service_creds_summary("1!11")
#print(access_creds)
#sh.add_user("aaa", "1.2.3.4", "1234", 11111, "en")
#print(access_creds)
pprint.pprint(sh.get_config_settings())
