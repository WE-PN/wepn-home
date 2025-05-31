#!/usr/bin/python


# This file is to upload CURRENT system state as a config to the backend
# It allows the apps to show the real state of the device, this migration should
# only happen during software update that adds that functionality

# At any point after the migration, source of truth for state of device should be backend
# We will add a method to indicate when a command fails (for example SSH does not turn on)

import json
import requests

import logging
import os
import pprint
import sys
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)

from services import Services
try:
    from self.configparser import ConfigParser as configparser
except ImportError:
    import configparser

CONFIG_FILE = '/etc/pproxy/config.ini'
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
device_id = config.get('django', 'id')
url = config.get('django', 'url') + "/api/device/" + device_id + "/"
print(url)
serial_number = config.get('django', 'serial_number'),
device_key = config.get('django', 'device_key'),
logger = logging.getLogger()
services = Services(logger)
cfg = services.get_config_string("0")
data = {
    "id": device_id,
    "serial_number": config.get('django', 'serial_number'),
    "device_key": config.get('django', 'device_key'),
    "config": cfg,
}
data_json = json.dumps(data)
headers = {"Content-Type": "application/json"}
try:
    response = requests.patch(url, data=data_json, headers=headers, timeout=10)
    pprint.pprint(response)
except requests.exceptions.RequestException as exception_error:
    print("Error in sending heartbeat: \r\n\t" + str(exception_error))
