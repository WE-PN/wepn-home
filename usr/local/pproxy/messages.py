from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import base64
import configparser
import json
import logging.config
import os
import requests

from constants import LOG_CONFIG, DEBUG_LOG_E2EE

CONFIG_FILE = '/etc/pproxy/config.ini'
STATUS_FILE = '/var/local/pproxy/status.ini'
GET_TIMEOUT = 10
logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)


class Messages():
    def __init__(self, logger=None) -> None:
        self.config = configparser.ConfigParser()
        self.config.read(CONFIG_FILE)
        self.status = configparser.ConfigParser()
        self.status.read(STATUS_FILE)
        self.pending_items = []
        if logger is not None:
            self.logger = logger
        else:
            self.logger = logging.getLogger("messages")
        pass

    def e2ee_available(self):
        # this can be expanded to check server capability
        return self.status.has_option('status', 'e2e_key')

    def get_messages(self, is_read=False, is_expired=False):
        url = self.config.get('django', 'url') + "/api/message/"
        data = {
            "serial_number": self.config.get('django', 'serial_number'),
            "device_key": self.config.get('django', 'device_key'),
            "is_read": is_read,
            "destination": "DEVICE",
            "is_expired": is_expired,
        }
        headers = {"Content-Type": "application/json"}
        data_json = json.dumps(data)
        response = requests.get(url, data=data_json, headers=headers, timeout=GET_TIMEOUT)
        all_messages = []
        for msg in response.json():
            self.pending_items.append(msg["id"])
            try:
                if "message_body" in msg and "is_secure" in msg["message_body"]:
                    if msg["message_body"]["is_secure"]:
                        # TODO: return this updated array instead
                        msg_nonce = str.encode(msg["message_body"]["nonce"])
                        msg_txt = msg["message_body"]["message"]
                        msg["message_body"]["decrypted"] = self.decrypt_message(msg_txt, msg_nonce)
            except KeyError as e:
                self.logger.warning("key not found:" + str(e))
            except Exception as d:
                self.logger.warning("key not found:" + str(d))
            all_messages.append(msg)
        return all_messages

    def mark_msg_read(self, id):
        url = self.config.get('django', 'url') + "/api/message/" + str(id) + "/"
        data = {
            "serial_number": self.config.get('django', 'serial_number'),
            "device_key": self.config.get('django', 'device_key'),
            "is_read": True,
        }
        headers = {"Content-Type": "application/json"}
        data_json = json.dumps(data)
        response = requests.patch(url, data=data_json, headers=headers, timeout=GET_TIMEOUT)
        if response.status_code != 200:
            self.logger.critical("Cannot mark message as read: " + str(response.content))
        try:
            self.pending_items.remove(id)
        except ValueError:
            self.logger.critical("Message " + str(id) + "was marked as read, but it was not pending")
        return response

    def send_msg(self, text, destination="APP", cert_id="", secure=True, msg_type="",
                 extra_fields=None, expires_at=None):
        # extra_fields: dict merged into message_body as-is, for structured
        # plain-text payloads (not encrypted, so only use with secure=False)
        # expires_at: expiry of the message record itself, set on the
        # message (not inside message_body)
        nonce = ""
        if secure:
            secure_text, nonce = self.encrypt_message(text)
            text = base64.urlsafe_b64encode(secure_text).decode("utf-8")
            nonce = base64.urlsafe_b64encode(nonce).decode("utf-8")
        message_body = {
            "message_type": msg_type,
            "message": text,
            "is_secure": secure,
            "cert_id": cert_id,
            "nonce": nonce
        }
        if extra_fields:
            message_body.update(extra_fields)
        url = self.config.get('django', 'url') + "/api/message/"
        data = {
            "serial_number": self.config.get('django', 'serial_number'),
            "device_key": self.config.get('django', 'device_key'),
            "message_body": message_body,
            "destination": destination.upper(),
            "is_read": False,
            "is_expired": False,
        }
        if expires_at is not None:
            data["expires_at"] = expires_at
        headers = {"Content-Type": "application/json"}
        data_json = json.dumps(data)
        response = requests.post(url, data=data_json, headers=headers, timeout=GET_TIMEOUT)
        if response.status_code != 201:
            self.logger.critical("Could not send message: " + str(response.text))

    def encrypt_message(self, private_msg):
        key = base64.urlsafe_b64decode(str(self.status.get('status', 'e2e_key')))
        nonce = os.urandom(12)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        ciphertext = encryptor.update(base64.b64encode(str.encode(private_msg))) + encryptor.finalize()
        # Tag (16 bytes) is appended to ciphertext; decrypt_message splits it back off.
        # NOTE: mobile app must append/strip tag symmetrically when this changes.
        return ciphertext + encryptor.tag, nonce

    def decrypt_message(self, encoded_encrypted_msg, nonce):
        key = base64.urlsafe_b64decode(str(self.status.get('status', 'e2e_key')))
        data = base64.urlsafe_b64decode(encoded_encrypted_msg)
        nonce = base64.urlsafe_b64decode(nonce)
        ciphertext, tag = data[:-16], data[-16:]
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        # finalize() verifies the tag and raises InvalidTag if the message was tampered with
        clear_b64 = decryptor.update(ciphertext) + decryptor.finalize()
        clear_text = base64.b64decode(clear_b64).decode("utf-8")
        if DEBUG_LOG_E2EE:
            self.logger.info("Decrypted message: " + clear_text)
        return clear_text
