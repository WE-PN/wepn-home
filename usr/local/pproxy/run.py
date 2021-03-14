from pproxy import PProxy
import time
from oled import OLED as OLED
from setup.onboard import OnBoard
import requests, json
import logging.config
from device import Device
import board
import adafruit_dotstar as dotstar

try:
    from self.configparser import configparser
except ImportError:
    import configparser

CONFIG_FILE='/etc/pproxy/config.ini'
STATUS_FILE='/var/local/pproxy/status.ini'
LOG_CONFIG="/etc/pproxy/logging.ini"
logging.config.fileConfig(LOG_CONFIG,
            disable_existing_loggers=False)
logger = logging.getLogger("startup")
logger.critical("Starting WEPN")

dots = dotstar.DotStar(board.D6, board.D5, 3, brightness=0.2)

oled = OLED()

config = configparser.ConfigParser()
config.read(CONFIG_FILE)
status = configparser.ConfigParser()
status.read(STATUS_FILE)
oled.set_led_present(config.get('hw','led'))
oled.clear_screen()
oled.show_logo()

dots.fill((255,0,0))
time.sleep(0.1)
dots.fill((180,75,0))
time.sleep(0.1)
dots.fill((105,150,0))
time.sleep(0.1)
dots.fill((30,225,0))
time.sleep(0.1)
dots.fill((105,150,0))
time.sleep(0.1)
dots.fill((0,240,15))
time.sleep(0.1)
dots.fill((0, 165, 90))
time.sleep(0.1)
dots.fill((0, 90, 165))
time.sleep(0.1)
dots.fill((0, 15, 240))
time.sleep(0.1)
dots.fill((60, 0, 195))
time.sleep(0.1)
dots.fill((105, 0, 150))
time.sleep(0.1)
dots.fill((180, 0, 75))
time.sleep(0.1)
dots.fill((255, 0, 30))
time.sleep(0.1)
dots.fill((0, 0, 0))



device = Device(logger)
gateway_vendor = device.get_default_gw_vendor()
logger.critical("Gateway vendor= " + str(gateway_vendor))
device.check_port_mapping_igd()

is_claimed = False
server_checkin_done = False
response = None
url_address = config.get('django','url') + "/api/device/is_claimed/"
data = json.dumps({'serial_number': config.get('django','serial_number')})
headers = {'Content-Type': 'application/json'}
try:
  response = requests.post(url_address, data=data, headers=headers)
  is_claimed = (response.status_code == 200)
  jresponse = json.loads(response.content)
  logger.error("is_claimed updated to " + str(is_claimed))
  server_checkin_done = True
except requests.exceptions.RequestException as exception_error:
    logger.exception("Error in connecting to server for claim status")


if 1 == int(status.get('status','claimed')):
    if not is_claimed and server_checkin_done:
        # server says device is not claimed, so wipe it
        logger.error("Server says unclaimed, locally cached claimed. Wiping")
        status['status']['mqtt-reason'] = '0'
        status['status']['claimed'] = '0'
        status['status']['mqtt'] = '0'
        status['status']['state'] = '3'
        with open(STATUS_FILE, 'w') as statusfile:
           status.write(statusfile)
        #reboot to go into onboarding
        oled.clear_screen()
        device.reboot()

    while True:
          try:
             PPROXY_PROCESS = PProxy()
             PPROXY_PROCESS.start()
          except Exception:
              logger.exception("Exception in main runner thread")
              del(PPROXY_PROCESS) 
              logger.debug("Retrying in 60 seconds ....")
              time.sleep(60)
              continue
          break
else:
    while True:
          try:
             ONBOARD = OnBoard()
             if is_claimed:
                 for name, key in status.items("previous_keys"):
                     logger.debug("Trying an old key: " + name + " , " + key)
                     ONBOARD.set_rand_key(key)
                     ONBOARD.start(True)
             ONBOARD.start()
          except Exception:
              logger.exception("Exception in onboarding")
              if ONBOARD:
                  del(ONBOARD) 
              logger.debug("Retrying in 60 seconds ....")
              time.sleep(60)
              continue
          break

