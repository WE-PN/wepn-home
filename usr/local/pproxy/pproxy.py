from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from os.path import basename
from threading import Lock, Thread
import atexit
import base64
import logging.config
import os
import queue
import random
import re
import shlex
import signal
import smtplib
import sys
import time
import zlib

try:
    from configparser import configparser
except ImportError:
    import configparser
try:
    import RPi.GPIO as GPIO
    from pad4pi import rpi_gpio
    gpio_up = True
except Exception as err:
    print("Error in GPIO: " + str(err))  # noqa: T201
    gpio_up = False

from device import Device
from diag import WPDiag
from heartbeat import HeartBeat
from ipw import IPW
from lcd import LCD as LCD
from led_client import LEDClient
from messages import Messages
from msg_channel import ChannelServer
from notify_limiter import NotificationLimiter
from port_probe import PortProbe
from services import Services
from wstatus import WStatus

from constants import (
    LOG_CONFIG,
    MSG_CHANNEL_QUEUE_MAX,
    NOTIFY_EMAIL_COOLDOWN_SECONDS,
    NOTIFY_PUSH_COOLDOWN_SECONDS,
    NOTIFY_RESPONSE_COOLDOWN_SECONDS,
)

COL_PINS = [26]  # BCM numbering
ROW_PINS = [19, 13, 6]  # BCM numbering
KEYPAD = [
    ["1", ], ["2", ], ["3"],
]
CONFIG_FILE = '/etc/pproxy/config.ini'
STATUS_FILE = '/var/local/pproxy/status.ini'
# actions that can take a long time (cert generation, email, per-friend
# work on an ip change) and are safe to overlap with fast commands; they
# run on their own single worker so a burst cannot block urgent actions
# like reboot_device. deliberately NOT here: update-pproxy/update-all/
# install-package (must not overlap code being replaced) and terminal
# actions (reboot_device, wipe_device), which jump the burst by design.
SLOW_ACTIONS = frozenset(["add_user", "delete_user"])
logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)

ipw = IPW()


class PProxy():
    def __init__(self, logger=None):
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.mqtt_connected = 0
        self.mqtt_reason = 0
        # bounded so a flood cannot exhaust memory; reject-on-full sheds load
        self.queue = queue.Queue(maxsize=MSG_CHANNEL_QUEUE_MAX)
        self.slow_queue = queue.Queue(maxsize=MSG_CHANNEL_QUEUE_MAX)
        self.channel = None
        self.loggers = {}
        if logger is not None:
            self.logger = logger
            self.loggers["heartbeat"] = logger
            self.loggers["diag"] = logger
            self.loggers["services"] = logger
            self.loggers["wstatus"] = logger
            self.loggers["device"] = logger
        else:
            self.logger = logging.getLogger("pproxy")
            self.loggers["heartbeat"] = logging.getLogger("heartbeat")
            self.loggers["diag"] = logging.getLogger("diag")
            self.loggers["services"] = logging.getLogger("services")
            self.loggers["wstatus"] = logging.getLogger("wstatus")
            self.loggers["device"] = logging.getLogger("device")
        if gpio_up:
            GPIO.cleanup()
            if GPIO.getmode() != 11:
                GPIO.setmode(GPIO.BCM)
            self.factory = rpi_gpio.KeypadFactory()
        else:
            self.factory = None
        self.leds = LEDClient()
        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
        self.status = WStatus(self.loggers['wstatus'])
        self.notify_limiter = NotificationLimiter(self.logger, self.status)
        self.device = Device(self.loggers['device'])
        self.mqtt_lock = Lock()
        self.lcd = None
        self.messages = Messages()
        self.port_probe = PortProbe(self.logger, self.device, self.messages)
        return

    def cleanup(self):
        self.logger.debug("PProxy shutting down.")
        if self.channel is not None:
            self.channel.stop()
        self.leds.blank()
        if self.lcd is not None:
            try:
                self.lcd.display([(1, "", 0, "black")], 20)
                self.lcd.set_backlight(turn_on=False)
            except Exception:
                self.logger.warning("LCD cleanup failed")
        if gpio_up:
            GPIO.cleanup()

    def set_logger(self, logger):
        self.logger = logger

    def set_loggers(self, index, logger):
        self.loggers[index] = logger

    def sanitize_str(self, str_in):
        # shlex.quote only escapes shell metacharacters; it leaves control
        # chars, zero-width spaces and other non-printable unicode (common
        # in app-supplied names) untouched, so they end up embedded verbatim
        # in things like access links. Strip those before quoting.
        printable = ''.join(ch for ch in str_in if ch.isprintable())
        return shlex.quote(printable)

    def get_response_cooldown(self):
        if self.config.has_section('notify') and self.config.has_option(
                'notify', 'response-cooldown'):
            try:
                return int(self.config.get('notify', 'response-cooldown'))
            except ValueError:
                pass
        return NOTIFY_RESPONSE_COOLDOWN_SECONDS

    def get_server_public_address(self):
        ip_address = self.sanitize_str(ipw.myip())
        if self.config.has_section(
                "dyndns") and self.config.getboolean('dyndns', 'enabled'):
            # we have good DDNS, lets use it
            server_address = self.config.get("dyndns", "hostname")
        else:
            server_address = ip_address
        return server_address

    def get_tunnel_from_data(self, data):
        if "config" in data and "tunnel" in data["config"]:
            tunnel = data["config"]["tunnel"]
        else:
            tunnel = "all"
        return tunnel

    def save_state(self, new_state, lcd_print=0, hb_send=True):
        with self.mqtt_lock:
            self.status.reload()
            self.status.set('state', new_state)
            self.status.set('mqtt', self.mqtt_connected)
            self.status.set('mqtt-reason', self.mqtt_reason)
            self.status.save()
        if hb_send:
            self.logger.debug('heartbeat from save_state ' + new_state)
            heart_beat = HeartBeat(self.loggers["heartbeat"])
            heart_beat.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
            heart_beat.send_heartbeat(lcd_print)

    def process_key(self, key):
        services = Services(self.loggers['services'])
        if (key == "1"):
            current_state = self.status.get('state')
            if (current_state == "2"):
                new_state = "1"
                services.stop()
            else:
                new_state = "2"
                services.start()
            self.save_state(str(new_state))
        # Run Diagnostics
        elif (key == "2"):
            diag = WPDiag(self.loggers['diag'])
            self.lcd.set_lcd_present(self.config.get('hw', 'lcd'))
            display_str = [(1, "Starting Diagnostics", 0, "green"),
                           (2, "please wait ...", 0, "green")]
            self.lcd.display(display_str, 15)
            diag.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
            test_port = int(self.config.get('openvpn', 'port')) + 1
            display_str = [(1, "Status Code", 0, "blue"), (2, str(
                diag.get_error_code(test_port)), 0, "blue")]
            self.lcd.display(display_str, 20)
            time.sleep(3)
            serial_number = self.config.get('django', 'serial_number')
            display_str = [(1, "Serial #", 0, "blue"),
                           (2, serial_number, 0, "white"), ]
            self.lcd.display(display_str, 19)
            time.sleep(5)
            display_str = [(1, "Local IP", 0, "blue"),
                           (2, self.device.get_local_ip(), 0, "white"), ]
            self.logger.info(display_str)
            self.lcd.display(display_str, 19)
            time.sleep(5)
            display_str = [(1, "MAC Address", 0, "blue"),
                           (2, self.device.get_local_mac(), 0, "white"), ]
            self.logger.debug(display_str)
            self.lcd.display(display_str, 19)
            time.sleep(5)
            heart_beat = HeartBeat(self.loggers["heartbeat"])
            heart_beat.set_mqtt_state(self.mqtt_connected, self.mqtt_reason)
            self.logger.debug('heartbeat from process_key 2')
            heart_beat.send_heartbeat()
        # Power off
        elif (key == "3"):
            services.stop()
            self.lcd.set_lcd_present(self.config.get('hw', 'lcd'))
            display_str = [(1, "Powering down", 0, "red"), ]
            self.lcd.display(display_str, 15)
            time.sleep(2)
            self.save_state("0", 0)
            self.lcd.show_logo()
            display_str = [(1, "", 0, "black"), ]
            time.sleep(2)
            self.lcd.display(display_str, 20)
            self.device.turn_off()

    def on_channel_message(self, source, payload):
        # called by the channel server for every 'msg' frame a producer sends.
        # returned status drives the ack: "queued"/"discarded" -> producer may
        # mark the backend message read; "busy" -> server sends an error frame
        # so the producer retries later (nothing is lost under overload).
        if not isinstance(payload, dict) or 'action' not in payload:
            self.logger.error("discarding invalid payload from " + str(source))
            return {"status": "discarded"}
        action = payload['action']
        if action == 'notification':
            # the command body lives on the messaging API; nudge the poller
            # to fetch it now instead of waiting for its next cycle
            th = Thread(target=self.trigger_fetch)
            th.start()
            return {"status": "queued"}
        # classify here so each lane is bounded independently and a full lane
        # is rejected before we ack (routing in the dispatcher could not).
        target = self.slow_queue if action in SLOW_ACTIONS else self.queue
        try:
            target.put_nowait((source, payload))
        except queue.Full:
            self.logger.warning("dispatch queue full, rejecting message from "
                                + str(source))
            return {"status": "busy"}
        return {"status": "queued"}

    def trigger_fetch(self):
        try:
            ack = self.channel.send_cmd("poller", {"cmd": "fetch-now"})
            if ack is None:
                self.logger.warning("poller not reachable for fetch-now, "
                                    "it will fetch on its next cycle")
        except Exception:
            self.logger.exception("failed to trigger message fetch")

    def dispatch_loop(self):
        # fast lane: one consumer, strict arrival order. slow actions are
        # classified onto the slow lane at intake (on_channel_message) so a
        # burst of per-friend work cannot block urgent commands here; each lane
        # stays FIFO but a fast action may overtake an earlier slow one
        # (see docs/message-channel.md)
        self.logger.info("message dispatcher started")
        while True:
            source, payload = self.queue.get()
            try:
                self.on_message_handler(payload, self.mqtt_lock)
            except Exception:
                self.logger.exception("error handling message from " + str(source))
            finally:
                self.queue.task_done()

    def slow_dispatch_loop(self):
        # slow lane: one worker, so slow actions stay strictly ordered among
        # themselves (e.g. add_user then delete_user for the same friend)
        self.logger.info("slow-action dispatcher started")
        while True:
            source, payload = self.slow_queue.get()
            try:
                self.on_message_handler(payload, self.mqtt_lock)
            except Exception:
                self.logger.exception("error handling slow message from "
                                      + str(source))
            finally:
                self.slow_queue.task_done()

    def send_mail(self, send_from, send_to,
                  subject, text, html, files_in,
                  server="127.0.0.1",
                  unsubscribe_link=None):
        if int(self.config.get('email', 'enabled')) == 0:
            # email is completely disabled
            self.logger.debug("Email feature is completely off.")
            return

        # prevent accidentally sending an empty email
        if text == "" and html == "":
            return

        html_option = False
        if (self.config.has_option('email', 'type') and
                self.config.get('email', 'type') == 'html'):
            html_option = True
        self.logger.info("preparing email")
        if not isinstance(files_in, list):
            files_in_list = [files_in]
        else:
            files_in_list = files_in

        if html_option:
            msg = MIMEMultipart('alternative')
        else:
            msg = MIMEMultipart()

        msg['From'] = send_from
        msg['To'] = send_to
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = subject

        if unsubscribe_link is not None:
            msg.add_header('List-Unsubscribe',
                           '<' + unsubscribe_link + '>')
            msg.add_header('List-Unsubscribe-Post',
                           'List-Unsubscribe=One-Click')
            template = open("ui/emails_template.txt", "r")
            email_txt = template.read()
            template.close()
            email_txt = email_txt.replace("{{text}}", text)
            email_txt = email_txt.replace("{{unsubscribe_link}}", unsubscribe_link)
            part1 = MIMEText(email_txt, 'plain')
        else:
            part1 = MIMEText(text, 'plain')

        msg.attach(part1)

        if html_option:
            template = open("ui/emails_template.html", "r")
            email_html = template.read()

            template.close()
            email_html = email_html.replace("{{text}}", html)
            email_html = email_html.replace("{{unsubscribe_link}}", unsubscribe_link)

            part2 = MIMEText(email_html, 'html')
            msg.attach(part2)

        if files_in_list is not None:
            for file_in in files_in_list:
                # TODO: security check: check if file_in is safe
                if (file_in is not None):
                    with open(file_in, "rb") as current_file:
                        part = MIMEApplication(
                            current_file.read(),
                            Name=basename(file_in)
                        )
                        part['Content-Disposition'] = 'attachment; filename="%s"' % basename(
                            file_in)
                        msg.attach(part)

        try:
            server = smtplib.SMTP(self.config.get('email', 'host'),
                                  self.config.get('email', 'port'))
            server.ehlo()
            server.starttls()
            server.login(self.config.get('email', 'username'),
                         self.config.get('email', 'password'))
            server.sendmail(send_from, send_to, msg.as_string())
            server.close()
            self.logger.info('successfully sent the mail')
        except Exception as error_exception:
            self.logger.error("failed to send mail: " + str(error_exception))

    # called by the channel server when a producer reports its link state
    # (today: the MQTT forwarder reporting broker connectivity)
    def on_channel_state(self, source, connected, reason):
        self.logger.info("channel state from " + str(source) + ": connected="
                         + str(connected) + " reason=" + str(reason))
        self.mqtt_connected = connected
        self.mqtt_reason = reason
        if connected:
            self.leds.blink(color=(0, 255, 0), wait=500, repetitions=1)
            self.leds.blank()
            # if device has too many friends, sending the heartbeat inside
            # save_state might take a while, so keep this callback fast
            th = Thread(target=self.save_state, args=("2",))
            th.start()
        else:
            # show solid yellow ring indicating MQTT has been
            # disconnected from server
            self.leds.pulse(color=(255, 255, 0),
                            wait=50,
                            repetitions=50)

    # prevent directory traversal attacks by checking final path

    def get_vpn_file(self, username):
        basedir = "/var/local/pproxy/"
        vpn_file = basedir + username + ".ovpn"
        if os.path.abspath(vpn_file).startswith(basedir):
            return vpn_file
        else:
            return None

    def on_message_handler(self, data, lock):
        # runs on the dispatch_loop thread, one message at a time, in the
        # order the producers delivered them
        services = Services(self.loggers['services'])
        unsubscribe_link = None
        send_email = True
        self.logger.debug(data)

        if ("uuid" in data and "subscribed" in data and "id" in data):
            us_id = self.sanitize_str(str(data['id']))
            if data['subscribed']:
                send_email = True
                us_flag = "false"
            else:
                send_email = False
                us_flag = "true"
            us_uuid = self.sanitize_str(data['uuid'])
            unsubscribe_link = self.config.get('django', 'url') + "/api/friend/" + us_id + "/subscribe/?uuid=" \
                + us_uuid + "&subscribe=" + us_flag + "&did=" + self.config.get('django', 'id')
            # print(unsubscribe_link)

        if (data['action'] == 'get-access-link'):
            cname = self.sanitize_str(data['cert_name'])
            short_link = services.get_short_link_text(cname,
                                                      self.get_server_public_address(),
                                                      self.get_tunnel_from_data(data))
            if short_link != "" and self.messages.e2ee_available() and \
                    self.notify_limiter.allow("response_access_link", cname,
                                              self.get_response_cooldown()):
                self.messages.send_msg(short_link, cert_id=cname, secure=True,
                                       msg_type="response-access-link")
        elif (data['action'] == 'get-error-log'):
            cname = self.sanitize_str(data['cert_name'])
            err_log = self.device.get_error_logs()
            contents_bytes = err_log.encode('utf-8')
            compressed = zlib.compress(contents_bytes)
            c_base = base64.b64encode(compressed).decode('utf-8')
            if self.notify_limiter.allow("response_error_logs", cname,
                                         self.get_response_cooldown()):
                self.messages.send_msg(c_base, cert_id=cname, secure=True,
                                       msg_type="response-error-logs")
        elif (data['action'] == 'show-e2ee-qrcode'):
            # This is useful for cases where the pod and the phone are somehow not able
            # to sync using local API, for example and isolated network.
            # This key should not be available from the device menu, to avoid a guest copying it.
            # TODO: this is currently just overwriting the screen
            # when LCD is handled by a separate service, we can give it a timeout.
            self.status.reload()
            if self.status.has_option('status', 'e2e_key'):
                e2ee_key = self.status.get('e2e_key')
            else:
                e2ee_key = "not-set"
            display_str = [(2, "wepn://e2ee_key=" + str(e2ee_key), 2, "white"), ]
            self.lcd.display(display_str, 19)
        elif (data['action'] == 'open-test-port'):
            # validation, single-session guard, and the 3-minute lifecycle
            # all live in PortProbe; request() only spawns and returns
            self.port_probe.request(data.get('port'))
        elif (data['action'] == 'add_user'):
            txt = None
            is_new_user = True
            try:
                self.logger.debug("before lock acquired")
                # light up ring LEDs in blue with fill pattern
                self.leds.spinning_wheel(color=(0, 0, 255),
                                         length=1,
                                         repetitions=100)
                lock.acquire()
                self.logger.debug("lock acquired")
                username = self.sanitize_str(data['cert_name'])
                server_address = self.get_server_public_address()
                tunnel = self.get_tunnel_from_data(data)
                try:
                    # extra sanitization to avoid path injection
                    lang = re.sub(r'\\\\/*\.?', "",
                                  self.sanitize_str(data['language']))
                except BaseException:
                    lang = 'en'
                self.logger.debug("Adding user: " + username +
                                  " with language:" + lang + " to " + tunnel)
                password = random.SystemRandom().randint(1111111111, 9999999999)
                if 'passcode' in data and 'email' in data:
                    if data['passcode'] and data['email']:
                        # TODO why re cannot remove \ even with escape?
                        # print("data=" + str(data))
                        data['passcode'] = re.sub(
                            r'[\\\\/*?:"<>|.]', "", data['passcode'][:25].replace("\n", ''))
                    else:
                        send_email = False
                else:
                    # if email not present or familiar phrase not set, no email!
                    send_email = False
                port = self.config.get('shadow', 'start-port')
                try:
                    is_new_user = services.add_user(
                        username, server_address, password, int(port), tunnel, lang)
                    if not is_new_user:
                        # getting an add for existing user? should be an ip change
                        self.logger.debug("Update IP")
                        self.device.update_dns(ipw.myip())
                    else:
                        # light up ring LEDs in blue with fill pattern
                        self.leds.fill_upto(color=(0, 0, 255),
                                            percentage=1,
                                            wait=50)
                        services.recover_missing_servers()
                    txt, html, attachments, subject = services.get_add_email_text(
                        username, server_address, lang, tunnel, is_new_user)
                except BaseException:
                    logging.exception("Error occured with adding user")
                    # blink led ring red for 6 times if add friend fails
                    self.leds.blink(color=(255, 0, 0),
                                    wait=200,
                                    repetitions=6)
                    send_email = False

                notify_token = None if is_new_user else server_address
                if txt is not None:
                    self.logger.debug("add_user: " + txt)
                    self.logger.debug("send_email?" + str(send_email))
                    email_cooldown = 0 if is_new_user else NOTIFY_EMAIL_COOLDOWN_SECONDS
                    if send_email and self.notify_limiter.allow(
                            "add_email", data['email'] + "|" + username,
                            email_cooldown, notify_token):
                        self.send_mail(send_from=self.config.get('email', 'email'),
                                       send_to=data['email'],
                                       subject=subject,
                                       text='The familiar phrase you have arranged with your friend is: ' +
                                       data['passcode'] + '\n' + txt,
                                       html='<p>The familiar phrase you have arranged with your friend is: <b>' +
                                       data['passcode'] + '</b></p>' + html,
                                       files_in=attachments,
                                       unsubscribe_link=unsubscribe_link)
                # alse send a message to the app via Messaging API
                short_link = services.get_short_link_text(username, server_address, tunnel)
                push_cooldown = 0 if is_new_user else NOTIFY_PUSH_COOLDOWN_SECONDS
                if short_link != "" and self.messages.e2ee_available() and \
                        self.notify_limiter.allow("user_added_msg", username,
                                                  push_cooldown, notify_token):
                    self.messages.send_msg(short_link, cert_id=username,
                                           secure=True, msg_type="user_added")

            except BaseException:
                self.logger.exception("Unhandled exception adding friend")
            finally:
                self.logger.debug("before lock released")
                lock.release()
                self.logger.debug("lock released")

        elif (data['action'] == 'delete_user'):
            username = self.sanitize_str(data['cert_name'])
            if not username:
                self.logger.error("username to be removed was empty")
                return
            self.logger.debug("Removing user: " + username)
            tunnel = self.get_tunnel_from_data(data)
            server_address = self.get_server_public_address()
            try:
                # show a blue led ring fill down pattern when
                # deleting a friend
                self.leds.fill_downfrom(color=(0, 0, 255),
                                        percentage=1,
                                        wait=50)
                services.delete_user(username, tunnel)
            except BaseException:
                self.logger.exception("delete friend failed!")
                # blink led red for 5 times if exception happens
                # during delete friend
                self.leds.blink(color=(255, 0, 0),
                                wait=50,
                                repetitions=5)
            if send_email and 'email' in data.keys() and data['email'] is not None and \
                    self.notify_limiter.allow("delete_email", data['email'] + "|" + username,
                                              NOTIFY_EMAIL_COOLDOWN_SECONDS):
                self.send_mail(send_from=self.config.get('email', 'email'),
                               send_to=data['email'],
                               subject="Your VPN details",
                               # 'Familiar phrase is '+ data['passcode'] +
                               text='\nAccess to VPN server IP address ' + server_address + ' is revoked.',
                               # '<p>Familiar phrase is <b>'+ data['passcode'] + '</b></p>'+
                               html="<p>Access to VPN server IP address <b>" + server_address +
                                    "</b> is revoked.</p>",
                               files_in=None,
                               unsubscribe_link=None)  # at this point, friend is removed from backend db
            # alse send a message to the app via Messaging API
            if self.notify_limiter.allow("user_deleted_msg", username, NOTIFY_PUSH_COOLDOWN_SECONDS):
                self.messages.send_msg("deleted user " + str(username) + " from " +
                                       str(server_address), cert_id=username, secure=False, msg_type="user_deleted")
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
        elif (data['action'] == 'install-package'):
            self.device.install_package(self.sanitize_str(data['package']))
        elif (data['action'] == 'set_creds'):
            if (data['host']):
                self.config.set('email', 'host', str(data['host']))
            self.config.set('email', 'port', str(data['port']))
            self.config.set('email', 'username', str(data['username']))
            self.config.set('email', 'email', str(data['email']))
            self.config.set('email', 'password', str(data['password']))
            with open(CONFIG_FILE, 'w') as configfile:
                self.config.write(configfile)

        elif (data['action'] == 'set_ddns'):
            allowed_params = ['enabled', 'hostname', 'url', 'username',
                              'password', 'method', 'zone_id', 'record_id', 'token']
            for item in allowed_params:
                if (item in data):
                    self.config.set('dyndns', item, str(data[item]))
            with open(CONFIG_FILE, 'w') as configfile:
                self.config.write(configfile)
        elif (data['action'] == 'config_update'):
            # TODO: these might require sanitization
            new_config = self.device.get_device_config_backend()
            # if local version is larger than remote, ignore messages as outdated
            # pass each part of the config to the right service handler
            self.logger.debug(new_config)
            # service_name = self.sanitize_str(data["service_name"])
            # config = data["config"]
            services.configure(new_config.get("config"))
        elif (data['action'] == 'wipe_device'):
            # very important action: make sure all VPN/ShadowSocks are deleted, and stopped
            # now reset the status bits
            # set led ring color to solid yellow
            self.leds.blink(color=(255, 255, 0), wait=250, repetitions=6)
            self.status.reload()
            self.status.set('mqtt', 0)
            self.status.set('mqtt-reason', 0)
            self.status.set('claimed', 0)
            self.status.save()
            self.save_state("3")
            # reboot to go into onboarding
            self.device.reboot()

    def fetch_config(self, services):
        try:
            new_config = self.device.get_device_config_backend()
            services.configure(new_config["config"])
        except:
            self.logger.exception("cannot fetch config")

    def start(self):
        self.lcd = LCD()
        self.lcd.set_lcd_present(self.config.get('hw', 'lcd'))
        # show a white spinning led ring
        # self.leds.set_all(color=(255, 255, 255))
        self.leds.spinning_wheel(color=(255, 255, 255),
                                 length=6,
                                 wait=50,
                                 repetitions=100)
        services = Services(self.loggers['services'])
        self.fetch_config(services)
        time.sleep(1)
        services.start()
        time.sleep(5)
        self.logger.debug('HW config: button=' + str(int(self.config.get('hw', 'buttons'))) + '  LCD=' +
                          self.config.get('hw', 'lcd'))
        if (int(self.config.get('hw', 'buttons')) == 1 and
                int(self.config.get('hw', 'button-version')) == 1):
            try:
                keypad = self.factory.create_keypad(
                    keypad=KEYPAD, row_pins=ROW_PINS, col_pins=COL_PINS)
                keypad.registerKeyPressHandler(self.process_key)
            except RuntimeError as er:
                self.logger.critical("setting up keypad failed: " + str(er))
                if gpio_up:
                    GPIO.cleanup()
        self.channel = ChannelServer(on_message=self.on_channel_message,
                                     on_state=self.on_channel_state,
                                     logger=self.logger)
        self.channel.start()
        try:
            heart_beat = HeartBeat(self.loggers["heartbeat"])
            heart_beat.send_heartbeat()
        except Exception as error:
            # producers keep feeding the queue even if the backend is
            # unreachable right now; the periodic heartbeat cron catches up
            self.logger.error("initial heartbeat failed: " + str(error))
        slow_worker = Thread(target=self.slow_dispatch_loop, daemon=True)
        slow_worker.start()
        # Blocking call: consumes producer messages in order until shutdown.
        self.dispatch_loop()
