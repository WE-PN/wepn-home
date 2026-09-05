#!/usr/bin/env python3

# Upload current system state as a config to the backend when the server has
# no config yet (null) and the locally saved config version is still 1 (default).
# This migration runs once, during the software update that introduces
# service-tiles functionality.

import json
import sys
import logging
import os
import requests

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)

import configparser
from services import Services

CONFIG_FILE = '/etc/pproxy/config.ini'
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

try:
    device_id = config.get('django', 'id')
    serial_number = config.get('django', 'serial_number')
    device_key = config.get('django', 'device_key')
    base_url = config.get('django', 'url')
except (configparser.NoSectionError, configparser.NoOptionError):
    sys.exit(0)

logger = logging.getLogger()
services = Services(logger)

if services.get_saved_server_config_version() != 1:
    print('config version above 1, config migration not necessary')
    sys.exit(0)

url = base_url + "/api/device/" + device_id + "/"
auth = {"serial_number": serial_number, "device_key": device_key}
headers = {"Content-Type": "application/json"}

try:
    response = requests.get(url, data=json.dumps(auth), headers=headers, timeout=10)
    server_data = json.loads(response.text)
    if server_data.get("config"):
        print('config exists in remote, config migration not necessary')
        sys.exit(0)
except requests.exceptions.RequestException as e:
    logger.error("Error fetching server config: " + str(e))
    sys.exit(1)

current_version = services.get_saved_server_config_version()
cfg = services.get_config_string(version=current_version)

# Fresh device (no config on the backend yet): enable measurements by default.
for service in cfg.get("services", []):
    if service.get("name") == "measurement":
        service.setdefault("settings", {})["enabled"] = True
        break

data = {
    "id": device_id,
    "serial_number": serial_number,
    "device_key": device_key,
    "config": cfg,
}
try:
    response = requests.patch(url, data=json.dumps(data), headers=headers, timeout=10)
    updated_config = json.loads(response.text)
    updated_version = updated_config.get("config", {}).get("config_version")
    if updated_version:
        services.save_server_config_version(updated_version)
    else:
        logger.error("Unexpected response from server during config migration: " + response.text)
except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
    logger.error("Error sending config to server: " + str(e))
