import json
from time import gmtime, strftime
import time
import ssl
import random
import os
import re
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
from ipw import IPW
import paho.mqtt.client as mqtt
from heartbeat import HeartBeat
from pad4pi import rpi_gpio
from oled import OLED as OLED
from diag import WPDiag
from services import Services
from device import Device
from wstatus import WStatus

COL_PINS = [26] # BCM numbering
ROW_PINS = [19,13,6] # BCM numbering
KEYPAD = [
        ["1",],["2",],["3"],
]
CONFIG_FILE='/etc/pproxy/config.ini'
STATUS_FILE='/var/local/pproxy/status.ini'

ipw =IPW()

class PProxy():
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.status = WStatus()
        self.device = Device()
        self.mqtt_connected = 0
        self.mqtt_reason = 0
        self.factory = rpi_gpio.KeypadFactory()
        return

    def __del__(self):
        print("PProxy shutting down.")
        self.factory.cleanup()


    def sanitize_str(self, str_in):
        return (shlex.quote(str_in))


    def save_state(self, new_state, led_print=1):
        self.status.set('state', new_state)
        self.status.save()
        print('heartbeat from save_state '+new_state)
        heart_beat = HeartBeat()
        heart_beat.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
        heart_beat.send_heartbeat(led_print)

    def process_key(self, key):
        services = Services()
        if (key == "1"):
            current_state=self.status.get('state')
            if (current_state == "2"):
                  new_state = "1"
                  services.stop()
            else:
                  new_state = "2"
                  services.start()
            self.save_state(str(new_state))
        #Run Diagnostics
        elif (key == "2"):
            led = OLED()
            diag = WPDiag()
            led.set_led_present(self.config.get('hw','led'))
            display_str = [(1, "Starting Diagnostics",0), (2, "please wait ...",0) ]
            led.display(display_str, 15)
            diag.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
            test_port=int(self.config.get('openvpn','port')) + 1
            if int(self.config.get('shadow','enabled'))==1:
                shadow = Shadow()
                test_port=int( shadow.get_max_port() ) + 2
            display_str = [(1, "Status Code",0), (2, str(diag.get_error_code( test_port )),0) ]
            led.display(display_str, 20)
            time.sleep(3)
            serial_number = self.config.get('django','serial_number')
            display_str = [(1, "Serial #",0), (2, serial_number,0), ]
            led.display(display_str, 19)
            time.sleep(5)
            display_str = [(1, "Local IP",0), (2, diag.get_local_ip(),0), ]
            print(display_str)
            led.display(display_str, 19)
            time.sleep(5)
            display_str = [(1, "MAC Address",0), (2, diag.get_local_mac(),0), ]
            print(display_str)
            led.display(display_str, 19)
            time.sleep(5)
            heart_beat = HeartBeat()
            heart_beat.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
            print('heartbeat from process_key 2')
            heart_beat.send_heartbeat()
        #Power off   
        elif (key == "3"):
            services.stop()
            led = OLED()
            led.set_led_present(self.config.get('hw','led'))
            display_str = [(1, "Powering down",0), ]
            led.display(display_str, 15)
            time.sleep(2)
            self.save_state("0",0)
            led.show_logo()
            display_str = [(1, "",0), ]
            time.sleep(2)
            led.display(display_str, 20)
            self.device.turn_off()

    def send_mail(self, send_from, send_to,
                  subject, text, files_in,
                  server="127.0.0.1"):

        if not isinstance(files_in, list):
            files_in_list = [files_in]
        else:
            files_in_list = files_in

        msg = MIMEMultipart()
        msg['From'] = send_from
        msg['To'] = send_to
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = subject

        msg.attach(MIMEText(text))

        if files_in_list != None:
            for file_in in files_in_list:
                if (file_in != None):
                    with  open(file_in, "rb") as current_file:
                        part = MIMEApplication(
                            current_file.read(),
                            Name=basename(file_in)
                        )
                        part['Content-Disposition'] = 'attachment; filename="%s"' % basename(file_in)
                        msg.attach(part)


        try:
            server = smtplib.SMTP(self.config.get('email', 'host'),
                                  self.config.get('email', 'port'))
            server.ehlo()
            server.starttls()
            server.login(self.config.get('email', 'username'), self.config.get('email', 'password'))
            server.sendmail(send_from, send_to, msg.as_string())
            server.close()
            print('successfully sent the mail')
        except Exception as error_exception:
            print("failed to send mail: "+ str(error_exception))

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(self, client, userdata, flags, result_code):
        print("Connected with result code "+str(result_code))
        self.mqtt_connected = 1
        self.mqtt_reason = result_code
        self.status.set('mqtt',1)
        self.status.set('mqtt-reason', result_code)
        self.status.save()

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        #client.subscribe("$SYS/#")
        topic = "devices/"+self.config.get('mqtt', 'username')+"/#"
        print('subscribing to: '+topic)
        client.subscribe(topic,qos=1)
        print('connected to service MQTT, saving state')
        self.save_state("2")


    #prevent directory traversal attacks by checking final path
    def get_vpn_file(self, username):
        basedir = "/var/local/pproxy/"
        vpn_file = basedir + username + ".ovpn"
        if os.path.abspath(vpn_file).startswith(basedir):
            return vpn_file
        else:
            return None

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        print("on_message: "+msg.topic+" "+str(msg.payload))
        try:
            data = json.loads(msg.payload)
        except:
            data = json.loads(msg.payload.decode("utf-8"))
        services = Services()
        if (data['action'] == 'add_user'):
            username = self.sanitize_str(data['cert_name'])
            try:
                # extra sanitization to avoid path injection
                lang = re.sub(r'\\\\/*\.?',"",self.sanitize_str(data['language']))
            except:
                lang = 'en'
            print("Adding user: "+ username +" with language:" + lang)
            ip_address = self.sanitize_str(ipw.myip())
            password = random.SystemRandom().randint(1111111111, 9999999999)
            #TODO why re cannot remove \ even with escape?
            data['passcode'] = re.sub(r'[\\\\/*?:"<>|.]',"",data['passcode'][:25].replace("\n",''))
            port = self.config.get('shadow','start-port')
            services.add_user(username, ip_address, password, int(port), lang)
            txt, html = services.get_add_email_text(username, ip_address, lang)
            print("add_user:"+txt)
            # TODO: this is not general enough, improve to assess if each service is enabled
            #       without naming OpenVPN explicitly
            if self.config.get('openvpn','email') == '1':
                vpn_file = self.get_safe_path(username)
                self.send_mail(self.config.get('email', 'email'), data['email'],
                           "Your VPN details",
                           'Familiar phrase is '+ data['passcode'] + '\n' + txt,
                           vpn_file)
            else:
                manuals = [ '/usr/local/pproxy/ui/'+lang+'/outline.png',
                            '/usr/local/pproxy/ui/'+lang+'/potatso.png']
                self.send_mail(self.config.get('email', 'email'), data['email'],
                           "Your VPN details",
                           'Familiar phrase is '+ data['passcode'] + '\n' + txt,
                           manuals)
 
        elif (data['action'] == 'delete_user'):
            username = self.sanitize_str(data['cert_name'])
            print("Removing user: "+username)
            ip_address = ipw.myip()
            services.delete_user(username)
            txt, html = services.get_add_email_text(username, ip_address)
            self.send_mail(self.config.get('email', 'email'), data['email'],
                           "Your VPN details",
                           "Access to VPN server IP address " +  ip_address + " is revoked",
                           None)
        elif (data['action'] == 'reboot_device'):
            self.save_state("3")
            self.device.reboot()
        elif (data['action'] == 'start_service'):
            services.start_all()
            self.save_state("2")
        elif (data['action'] == 'stop_service'):
            services.stop_all()
            self.save_state("1")
        elif (data['action'] == 'restart_service'):
            services.restart_all()
        elif (data['action'] == 'reload_service'):
            services.reload_all()
        elif (data['action'] == 'update-pproxy'):
            self.device.update()
        elif (data['action'] == 'update-all'):
            self.device.update_all()
        elif (data['action'] == 'set_creds'):
            if (data['host']):
                self.config.set('email', 'host', self.sanitize_str(data['host']))
            self.config.set('email', 'port', self.sanitize_str(data['port']))
            self.config.set('email', 'username', self.sanitize_str(data['username']))
            self.config.set('email', 'email', self.sanitize_str(data['email']))
            self.config.set('email', 'password', self.sanitize_str(data['password']))
            with open(CONFIG_FILE, 'w') as configfile:
                self.config.write(configfile)
        elif (data['action'] == 'wipe_device'):
            #very important action: make sure all VPN/ShadowSocks are deleted, and stopped
            #now reset the status bits
            self.status.set('mqtt',0)
            self.status.set('mqtt-reason',0)
            self.status.set('claimed',0)
            self.status.save()
            self.save_state("3")
            #reboot to go into onboarding
            self.device.reboot()
        print("incoming data:"+str(data))
    #callback for diconnection of MQTT from server
    def on_disconnect(self,client, userdata, reason_code):
        print("MQTT disconnected")
        self.mqtt_connected = 0
        self.mqtt_reason = reason_code
        self.status.set('mqtt',0)
        self.status.set('mqtt-reason',reason_code)
        self.status.save()


    def start(self):
        oled = OLED()
        oled.set_led_present(self.config.get('hw','led'))
        oled.show_logo()
        services = Services()
        services.start()
        client = mqtt.Client(self.config.get('mqtt', 'username'), clean_session=False)
        print('HW config: button='+str(int(self.config.get('hw','buttons'))) + '  LED='+
                self.config.get('hw','led'))
        if (int(self.config.get('hw','buttons'))):
            keypad = self.factory.create_keypad(keypad=KEYPAD, row_pins=ROW_PINS, col_pins=COL_PINS)
            keypad.registerKeyPressHandler(self.process_key)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        client.tls_set("/etc/ssl/certs/DST_Root_CA_X3.pem", tls_version=ssl.PROTOCOL_TLSv1_2)
        rc= client.username_pw_set(username=self.config.get('mqtt', 'username'),
                               password=self.config.get('mqtt', 'password'))
        print("mqtt host:" +str(self.config.get('mqtt','host')))
        try:
            rc=client.connect(str(self.config.get('mqtt', 'host')),
                       int(self.config.get('mqtt', 'port')),
                       int(self.config.get('mqtt', 'timeout')))

        except Exception as error:
            print("MQTT connect failed")
            display_str = [(1, chr(33)+'     '+chr(33),1), (2, "Network error,",0), (3, "check cable...", 0) ]
            oled.display(display_str, 15)
            if (int(self.config.get('hw','buttons'))):
                keypad.cleanup()
            raise
        # Blocking call that processes network traffic, dispatches callbacks and
        # handles reconnecting.
        # Other loop*() functions are available that give a threaded interface and a
        # manual interface.
        client.loop_forever()
        if (int(self.config.get('hw','buttons'))):
            keypad.cleanup()