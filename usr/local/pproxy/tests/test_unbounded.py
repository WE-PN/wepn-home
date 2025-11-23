import logging
import os
import sys

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)


from unbounded import Unbounded

logger = logging.getLogger("led_manager")
ub = Unbounded(logger)
print(ub.get_overlayable_config_value("bw-limit", "x"))
print(ub.is_enabled())
ub.recover_missing_servers()
ub.start()
ub.stop()

