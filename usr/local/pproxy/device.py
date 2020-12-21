
import json
from time import gmtime, strftime
import time
import ssl
import random
from getmac import get_mac_address
import logging.config
import netifaces
import atexit
import upnpclient as upnp
try:
    from self.configparser import configparser
except ImportError:
    import configparser

import smtplib
from os.path import basename
import subprocess
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate
from subprocess import call
import shlex
#import ipw
import ipw
import paho.mqtt.client as mqtt
from pad4pi import rpi_gpio
from oled import OLED as OLED
from wstatus import WStatus as WStatus

COL_PINS = [26] # BCM numbering
ROW_PINS = [19,13,6] # BCM numbering
KEYPAD = [
        ["1",],["2",],["3"],
]
CONFIG_FILE='/etc/pproxy/config.ini'
PORT_STATUS_FILE='/var/local/pproxy/port.ini'


class Device():
    def __init__(self, logger):
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.status = WStatus(logger, PORT_STATUS_FILE)
        self.logger = logger
        self.correct_port_status_file()
        self.igds = []
        self.iface = str(self.config.get('hw','iface'))
        atexit.register(self.cleanup)

    def find_igds(self):
        devices = upnp.discover()
        self.logger.info("upnp devices:" + str(devices))
        for d in devices:
            if "InternetGatewayDevice" in d.device_type:
               try:
                    self.logger.critical("IGD found: " + str(d.model_name) +\
                            "-" + str(d.manufacturer)+ "-"+ str(d.location))
               except Exception as err:
                    self.logger.critical("IGD found, missing attributes")
                    print(err)
                    pass
               self.igds.append(d)

    def check_igd_supports_portforward(self, igd):
        l3forward_supported = False
        wanipconn_supported = False
        for service in igd.services:
            if "Layer3Forwardingg" in service.service_id:
                l3forward_supported = True
            if "WANIPConn" in service.service_id:
                wanipconn_supported = True
        if not l3forward_supported:
            self.logger.error("Error: could not find L3 forwarding")
        if not wanipconn_supported:
            self.logger.error("Error: could not find WANIPConn")
        return (l3forward_supported and wanipconn_supported)

    def correct_port_status_file(self):
        if not self.status.has_section('port-fwd'):
            self.status.add_section('port-fwd')
            self.status.set_field('port-fwd','fails','0')
            self.status.set_field('port-fwd','fails-max','3')
            self.status.set_field('port-fwd','skipping','0')
            self.status.set_field('port-fwd','skips','0')
            self.status.set_field('port-fwd','skips-max','20')
            self.status.save()

    def cleanup(self):
        if self.status is not None:
            self.status.save()

    def sanitize_str(self, str_in):
        return (shlex.quote(str_in))

    def execute_cmd(self, cmd):
        try:
            failed = 0
            args = shlex.split(cmd)
            process = subprocess.Popen(args)
            sp = subprocess.Popen(args, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE)
            out, err = sp.communicate()
            process.wait()
            #if out:
            #    print ("standard output of subprocess:")
            #    print (out)
            if err:
                failed+=1
                #print ("standard error of subprocess:")
                #print (err)
                #print ("returncode of subprocess:")
                #print ("returncode="+str(sp.returncode))
            # Does not work: return sp.returncode
            return failed
        except Exception as error_exception:
            self.logger.error(args)
            self.logger.error("Error happened in running command:" + cmd)
            self.logger.error("Error details:\n"+str(error_exception))
            process.kill()
            return 99 



    def turn_off(self):
        cmd = "sudo /sbin/poweroff"
        self.execute_cmd(cmd)

    def restart_pproxy_service(self):
        cmd = "sudo /usr/local/sbin/restart-pproxy.sh"
        self.execute_cmd(cmd)

    def reboot(self):
        cmd = "sudo /sbin/reboot"
        self.execute_cmd(cmd)

    def update(self):
        cmd = "sudo /bin/sh /usr/local/sbin/update-pproxy.sh"
        self.execute_cmd(cmd)

    def update_all(self):
        cmd = "sudo /bin/sh /usr/local/sbin/update-system.sh"
        self.execute_cmd(cmd)

    def open_port(self, port, text):
        skip = int(self.status.get_field('port-fwd','skipping'))
        skip_count = int(self.status.get_field('port-fwd','skips'))
        if skip:
            if skip_count < int(self.status.get_field('port-fwd','skips-max')):
                # skip, do nothing just increase cound
                skip_count += 1
                self.status.set_field('port-fwd','skips', str(skip_count))
            else:
                # skipped too much, try open port again in case it works
                self.status.set_field('port-fwd','skipping', '0')
                self.status.set_field('port-fwd','skips', '0')
        else:
            # no skipping, just try opening port normally with UPNP
            self.set_port_forward("open", port, text)
        self.logger.info("skipping? " + str(skip) + " count=" + str(skip_count))

    def close_port(self, port):
        skip = int(self.status.get_field('port-fwd','skipping'))
        skip_count = int(self.status.get_field('port-fwd','skips'))
        if skip:
            if skip_count < int(self.status.get_field('port-fwd','skips-max')):
                # skip, do nothing just increase cound
                skip_count += 1
                self.status.set_field('port-fwd','skips', str(skip_count))
            else:
                # skipped too much, try open port again in case it works
                self.status.set_field('port-fwd','skipping', '0')
                self.status.set_field('port-fwd','skips', '0')
            self.status.save()
        else:
            # no skipping, just try opening port normally with UPNP
            self.set_port_forward("close", port, "")
        self.logger.info("skipping?" + str(skip) + " count=" + str(skip_count))

    def set_port_forward(self, open_close, port, text):
        failed = 0
        local_ip = self.get_local_ip()
        if not self.igds:
            self.find_igds()
        for igd in self.igds:
            try:
                if open_close == "open":
                    igd.WANIPConn1.AddPortMapping(
                            NewRemoteHost='0.0.0.0',
                            NewExternalPort=port,
                            NewProtocol='TCP',
                            NewInternalPort=port,
                            NewInternalClient=str(local_ip),
                            NewEnabled='1',
                            NewPortMappingDescription=str(text),
                            NewLeaseDuration=10000)

                    igd.WANIPConn1.AddPortMapping(
                            NewRemoteHost='0.0.0.0',
                            NewExternalPort=port,
                            NewProtocol='UDP',
                            NewInternalPort=port,
                            NewInternalClient=str(local_ip),
                            NewEnabled='1',
                            NewPortMappingDescription=str(text),
                            NewLeaseDuration=10000)
                else:
                    igd.WANIPConn1.DeletePortMapping(
                            NewRemoteHost='0.0.0.0',
                            NewExternalPort=port,
                            NewProtocol='TCP')
                    igd.WANIPConn1.DeletePortMapping(
                            NewRemoteHost='0.0.0.0',
                            NewExternalPort=port,
                            NewProtocol='UDP')
            except Exception as err:
                self.logger.error("Port forward operation failed: "+str(e))
                failed += 1

        # if we failed, check to see if max-fails has passed
        fails = int(self.status.get_field('port-fwd','fails'))
        if failed > 0:
            self.logger.error("PORT MAP FAILED")
            if fails >= int(self.status.get_field('port-fwd','fails-max')):
                # if passed limit, reset fail count, 
                self.status.set_field('port-fwd','fails', 0 )
                # indicate next one is going to be skip
                self.status.set_field('port-fwd','skipping', 1 )
            else:
                # failed, but has not passed the threshold
                fails += failed
                self.status.set_field('port-fwd','fails', str(fails))

    def get_local_ip(self):
        try:
            ip = netifaces.ifaddresses(self.iface)[netifaces.AF_INET][0]['addr']
            return ip
        except Exception as error_exception:
            self.logger.error("Error happened in getting my IP")
            self.logger.error("Error details:\n"+str(error_exception))
            return '0.0.0.0'


    def get_local_mac(self):
       try:
          mac = netifaces.ifaddresses(self.iface)[netifaces.AF_LINK][0]['addr']
       except KeyError:
          pass
          mac= ""; 
       return mac

    def get_default_gw_ip(self):
        try:
            gws = netifaces.gateways()
            return gws['default'][netifaces.AF_INET][0]
        except Exception as error_exception:
            self.logger.error("Error happened in getting gateway IP")
            self.logger.error("Error details:\n"+str(error_exception))
            return '0.0.0.0'

    def get_default_gw_mac(self):
        gw_ip = self.get_default_gw_ip()
        try:
            gw_mac = get_mac_address(ip=gw_ip)
            return gw_mac
        except Exception as error_exception:
            self.logger.error("Error happened in getting gateway IP")
            self.logger.error("Error details:\n"+str(error_exception))
            return '0.0.0.0'
    def get_default_gw_vendor(self):
        return self.get_default_gw_mac()[:8]
