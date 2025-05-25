import sys
import os
import logging
up_dir = os.path.dirname(os.path.abspath(__file__))+'/../'
sys.path.append(up_dir)

from ipw import IPW
from shadow import Shadow
from openvpn import OpenVPN
from services import Services
from device import Device

l = logging.getLogger()
ipw = IPW()
s   = Shadow(l)
o   = OpenVPN(l)
a   = Services(l)
device = Device(l)

##s.stop_all()

#s.add_user("mycert"name"51","0","mypass", 5134, 'en')
#a.add_user("mycert"name"51","127.0.0.1","mypass", 5134)
#s.add_user("mycert"name"21","mypass", 2134)
#s.add_user("mycert"name"31","mypass", 3134)#


ip_address = ipw.myip()
#print(">>>>>>>>>>>> adding user ue.mp")
#print(a.get_service_creds_summary("1.2.3.4"))
#s.add_user("ue.mp", ip_address ,"9628834282", 4000, 'en')#
#print(s.get_add_email_text("ue.mp",ip_address,"en"))
#print(">>>>>>>>>>>> looking at list ")
#device.execute_cmd('/usr/bin/upnpc -l > /tmp/a')
#print(">>>>>>>>>>>> deleting test users")
#s.delete_user("mycert"name"11")
#s.delete_user("mycert"name"51")
#s.delete_user("mycert"name"21")
#o.delete_user("mycert"name"51")
#s.start()
#s.stop()

#print(">>>>>>>>>>getting email text for test user")

#txt, html, attachments, subject = a.get_add_email_text("mycert"name"51",ip_address, 'en') 
#print ("text is = " +txt)
#print("deleting user ue.mp")
#s.self_test()
#s.delete_user("ue.mp")#
#device.execute_cmd('/usr/bin/upnpc -l > /tmp/b')

# example from g.wepn.dev/config-versioning
cfg= {
   "config_version": 1724134943,
   "services": [
      {
         "name": "Tor",
         "settings": {
            "enabled": True,
            "port": 123,
            "mode": "bridge",
         },
      },
      {
         "name": "WireGuard",
         "settings": {
            "enabled": True,
            "port": 123,
         },
      },
      {
         "name": "Shadowsocks",
         "settings": {
            "enabled": True,
            "port": 123,
            "manualPrefix": "ABCD",
            "portRangeStart": 4000,
            "autoPrefixSelection": False,
         },
      },
      {
         "name": "OONI",
         "settings": {
            "enabled": True,
            "probeEnabled": False,
            "reportCollectorPort": 0,
            "reportCollectorEnabled": False,
         },
      },
      {
         "name": "SSH",
         "settings": {
            "enabled": True,
            "port": 2222,
         },
         "secure_settings": {
            "nonce": "asdasdaiu32352sada",
            "data":  "asdasdasdasdasdNCAwdGTrwXx7Voqg82bOjIAHkmMKxk_rmMHzQTzSQHj",
         },
      },
      {
         "name": "WiFi",
         "settings": {
            "enabled": True,
            "autoconnect": True,
         },
         "secure_settings": {
            "nonce": "kajshdaiuweyiu32352sada",
            "data": "xO1qVOMsLmfZd7U8cCXaoI4ESg3HNCfQl2PbcDcr9GGhj7zky5fHZBZZv9gwsP0ABWg30AOe9iAH7AwJMfV6CVVj2mfa1vu7p-4Vsc_dtSgd8HYgKRnY5jtG3Qhbs9DQD2W_2-LLUDlFtvIH52ZmCNCAwdGTrwXx7Voqg82bOjIAHkmMKxk_rmMHzQTzSQHj",
         },
      },
   ],
}

from pprint import pprint
a.configure(cfg)
pprint(a.get_config_string())
