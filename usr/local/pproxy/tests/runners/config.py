import json
import sys
import os
import logging.config
import logging
import os
from pprint import pprint
up_dir = os.path.dirname(os.path.abspath(__file__))+'/../'
sys.path.append(up_dir)


from service import Service
from wstatus import WStatus as wstatus
from shadow import Shadow
from tor import Tor

LOG_CONFIG="/etc/pproxy/logging-debug.ini"
logging.config.fileConfig(LOG_CONFIG,
            disable_existing_loggers=False)

logger = logging.getLogger("shadowsocks")
#s=Service("shadowsocks",logger)
#js = s.get_config_settings()
#pprint(json.dumps(js))
#js["settings"]["enabled"]=False
#s.apply_config_settings(json.dumps(js))
#w=wstatus(logger)
#pprint(w.get_service_status("shadowsocks"))

#ss=Shadow(logger)
#current_conf=json.loads(json.dumps(ss.get_config_settings()))
#print(f"overlayed value is {ss.get_start_port()}")
#current_conf["settings"]["start-port"]=8100
#pprint(current_conf)
#ss.apply_config_settings(json.dumps(current_conf["settings"]))


t=Tor(logger)
print(t.get_port())
