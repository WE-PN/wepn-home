
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open, call, PropertyMock
import datetime
import json

# Mock missing modules BEFORE importing device
requests_mock = MagicMock()
class MockConnectionError(Exception): pass
requests_mock.exceptions.ConnectionError = MockConnectionError
sys.modules['requests'] = requests_mock

sys.modules['getmac'] = MagicMock()
sys.modules['pystemd'] = MagicMock()
sys.modules['pystemd.systemd1'] = MagicMock()
sys.modules['distro'] = MagicMock()
sys.modules['netifaces'] = MagicMock()
sys.modules['psutil'] = MagicMock()
sys.modules['upnpclient'] = MagicMock()
sys.modules['packaging'] = MagicMock()
sys.modules['packaging.version'] = MagicMock()

# Add parent directory to path to import device
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import device
from device import Device

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def mock_config():
    with patch('device.configparser.ConfigParser') as mock:
        instance = mock.return_value
        instance.get.return_value = 'some_value'
        instance.getint.return_value = 0
        instance.getboolean.return_value = False
        def get_side_effect(section, option):
            if option == 'id': return '123'
            if option == 'username': return 'user'
            if option == 'password': return 'pass'
            if option == 'hostname': return 'host'
            if option == 'url': return 'http://url'
            if option == 'serial_number': return 'SN123'
            if option == 'device_key': return 'DK123'
            if option == 'enabled': return 'True'
            return 'some_value'
        instance.get.side_effect = get_side_effect
        yield mock

@pytest.fixture
def mock_wstatus():
    with patch('device.WStatus') as mock:
        yield mock

@pytest.fixture
def device_instance(mock_logger, mock_config, mock_wstatus):
    with patch('device.atexit.register'):
        dev = Device(mock_logger)
        return dev

def test_random_cron_delay(mock_logger):
    with patch('device.time.sleep') as mock_sleep:
        device.random_cron_delay([])
        mock_sleep.assert_not_called()
        device.random_cron_delay(['--random-delay'])
        mock_sleep.assert_called()
        with patch('device.getopt.getopt', side_effect=device.getopt.GetoptError("err")):
             mock_sleep.reset_mock()
             device.random_cron_delay(['-x'])
             mock_sleep.assert_not_called()

class TestDeviceInit:
    def test_init(self, mock_logger, mock_config, mock_wstatus):
        with patch('device.atexit.register') as mock_atexit:
            dev = Device(mock_logger)
            assert dev.logger == mock_logger
            assert dev.config == mock_config.return_value
            mock_config.return_value.read.assert_called_with('/etc/pproxy/config.ini')
            assert mock_wstatus.call_count == 2 
            mock_atexit.assert_called_with(dev.cleanup)
            
    def test_correct_port_status_file(self, device_instance):
        device_instance.port_status.has_section.return_value = False
        device_instance.port_status.has_option.return_value = False
        device_instance.correct_port_status_file()
        device_instance.port_status.add_section.assert_called_with('port-fwd')
        device_instance.port_status.save.assert_called()

    def test_cleanup(self, device_instance):
        device_instance.cleanup()
        device_instance.port_status.save.assert_called()

class TestUPnP:
    @patch('device.upnp.discover')
    def test_find_igds_success(self, mock_discover, device_instance):
        mock_igd = MagicMock()
        mock_igd.device_type = "InternetGatewayDevice"
        mock_service = MagicMock()
        mock_action = MagicMock()
        mock_action.name = "AddPortMapping"
        mock_service.actions = [mock_action]
        mock_igd.services = [mock_service]
        mock_discover.return_value = [mock_igd]
        device_instance.find_igds()
        assert len(device_instance.igds) == 1
        
        mock_bad = MagicMock()
        mock_bad.device_type = "InternetGatewayDevice"
        p = PropertyMock(side_effect=Exception("err"))
        type(mock_bad).friendly_name = p
        mock_discover.return_value = [mock_bad]
        device_instance.find_igds()
        assert device_instance.logger.exception.called

    @patch('device.upnp.discover')
    def test_find_igds_no_igd(self, mock_discover, device_instance):
        mock_device = MagicMock()
        mock_device.device_type = "MediaRenderer"
        mock_discover.return_value = [mock_device]
        device_instance.find_igds()
        assert len(device_instance.igds) == 0

    def test_check_port_mapping_igd(self, device_instance):
        with patch.object(device_instance, 'find_igds'), \
             patch.object(device_instance, 'check_igd_supports_portforward', return_value=True):
             device_instance.igds = [MagicMock()]
             device_instance.port_mappers = [MagicMock()]
             assert device_instance.check_port_mapping_igd() is True
             device_instance.igds = []
             assert device_instance.check_port_mapping_igd() is False
             device_instance.check_igd_supports_portforward.side_effect = Exception("err")
             device_instance.igds = [MagicMock()]
             device_instance.port_mappers = [] 
             assert device_instance.check_port_mapping_igd() is False

    def test_should_skip_upnp(self, device_instance):
        device_instance.port_status.get_field.side_effect = ['0', '', '0', '20'] 
        assert device_instance.should_skip_upnp() == 0
        real_now = datetime.datetime(2020, 1, 1)
        future_date = datetime.datetime(2025, 1, 1)
        with patch.object(device_instance, 'get_safe_skipping_start_date', return_value=future_date), \
             patch('device.datetime') as mock_dt:
             mock_dt.datetime.now.return_value = real_now
             device_instance.port_status.get_field.side_effect = ['1', '0', '20']
             assert device_instance.should_skip_upnp() == 1
             device_instance.port_status.set_field.assert_called_with('port-fwd', 'skips', '1')
        with patch.object(device_instance, 'get_safe_skipping_start_date', return_value=future_date), \
             patch('device.datetime') as mock_dt:
             mock_dt.datetime.now.return_value = real_now
             device_instance.port_status.get_field.side_effect = ['1', '21', '20']
             assert device_instance.should_skip_upnp() is False
             device_instance.port_status.set_field.assert_any_call('port-fwd', 'skipping', '0')

    def test_open_port(self, device_instance):
        with patch.object(device_instance, 'should_skip_upnp', return_value=False):
            with patch.object(device_instance, 'set_port_forward', return_value=True) as mock_spf:
                result = device_instance.open_port(8080, "test")
                assert result is True
                mock_spf.assert_called_with("open", 8080, "test", 8080, device.DEFAULT_UPNP_TIMEOUT)

    def test_close_port(self, device_instance):
        with patch.object(device_instance, 'should_skip_upnp', return_value=False):
            with patch.object(device_instance, 'set_port_forward') as mock_spf:
                device_instance.close_port(8080)
                mock_spf.assert_called_with("close", 8080, "")

    def test_check_igd_supports_portforward(self, device_instance):
        mock_igd = MagicMock()
        s1 = MagicMock(); s1.service_id = "Layer3Forwarding"
        s2 = MagicMock(); s2.service_id = "WANIPConn"
        mock_igd.services = [s1, s2]
        assert device_instance.check_igd_supports_portforward(mock_igd) is True
        mock_igd.services = []
        assert device_instance.check_igd_supports_portforward(mock_igd) is False

    def test_set_port_forward_success_open(self, device_instance):
        mock_pm = MagicMock()
        mock_pm.AddPortMapping.return_value = True
        device_instance.port_mappers = [mock_pm]
        device_instance.igds = [MagicMock()]
        with patch.object(device_instance, 'get_local_ip', return_value="1.2.3.4"):
            device_instance.set_port_forward("open", 8080, "test")
            assert mock_pm.AddPortMapping.call_count == 2
            
    def test_set_port_forward_all_paths(self, device_instance):
        mock_pm = MagicMock()
        device_instance.port_mappers = [mock_pm]
        device_instance.igds = [MagicMock()]
        with patch.object(device_instance, 'get_local_ip', return_value="1.2.3.4"):
            mock_pm.DeletePortMapping.return_value = True
            device_instance.set_port_forward("close", 8080, "")
            assert mock_pm.DeletePortMapping.call_count == 2
            
            device.upnp.soap.SOAPError = Exception 
            mock_pm.AddPortMapping.side_effect = Exception("fail")
            device_instance.port_status.get_field.side_effect = lambda s, f: '5' if f == 'fails' else '3'
            device_instance.set_port_forward("open", 8080, "test", retry=True)
            device_instance.port_status.set_field.assert_any_call('port-fwd', 'skipping', 1)

    def test_get_port_mapping_by_index_real(self, device_instance):
        mock_pm = MagicMock()
        mock_action = MagicMock()
        mock_action.name = "GetGenericPortMappingEntry"
        mock_pm.actions = [mock_action]
        
        with patch.object(device_instance, 'find_igds') as mock_find:
            device_instance.port_mappers = [mock_pm]
            device_instance.igds = [MagicMock()] 
            mock_pm.GetGenericPortMappingEntry.return_value = {"NewExternalPort": 80}
            
            res = device_instance.get_port_mapping_by_index(0)
            assert res["NewExternalPort"] == 80
            
            mock_pm.GetGenericPortMappingEntry.side_effect = device.upnp.soap.SOAPError("err")
            with pytest.raises(device.upnp.soap.SOAPError):
                device_instance.get_port_mapping_by_index(0)

    def test_get_all_port_mappings(self, device_instance):
        device_instance.get_port_mapping_by_index = MagicMock(side_effect=[
            {"NewExternalPort": 80, "NewPortMappingDescription": "one"}, 
            {"NewExternalPort": 443, "NewPortMappingDescription": "WEPN HTTPS"},
            {"NewExternalPort": 22, "NewPortMappingDescription": "SSH"},
            None
        ])
        ports, index = device_instance.get_all_port_mappings()
        assert len(ports) == 1
        assert index == 3

        device_instance.get_port_mapping_by_index = MagicMock(side_effect=device.upnp.soap.SOAPError("err"))
        ports, index = device_instance.get_all_port_mappings()
        assert index == 0

    def test_get_port_mapping_by_port(self, device_instance):
        mock_pm = MagicMock()
        mock_action = MagicMock()
        mock_action.name = "GetSpecificPortMappingEntry"
        mock_pm.actions = [mock_action]
        mock_pm.GetSpecificPortMappingEntry.return_value = "Entry"
        device_instance.port_mappers = [mock_pm]
        device_instance.igds = [MagicMock()]
        res = device_instance.get_port_mapping_by_port(80)
        assert res == "Entry"

class TestNetwork:
    @patch('device.netifaces')
    def test_get_local_ip(self, mock_netifaces, device_instance):
        mock_netifaces.AF_INET = 2
        mock_netifaces.ifaddresses.return_value = {2: [{'addr': '192.168.1.100'}]}
        mock_netifaces.interfaces.return_value = ['eth0']
        device_instance.iface = 'eth0'
        ip = device_instance.get_local_ip()
        assert ip == '192.168.1.100'
        mock_netifaces.ifaddresses.side_effect = Exception("error")
        assert device_instance.get_local_ip() == '127.0.0.1'

    @patch('device.get_mac_address')
    def test_get_default_gw_mac_vendor(self, mock_gma, device_instance):
        with patch.object(device_instance, 'get_default_gw_ip', return_value='1.2.3.4'):
            mock_gma.return_value = "00:11:22:33:44:55"
            assert device_instance.get_default_gw_mac() == "00:11:22:33:44:55"
            assert device_instance.get_default_gw_vendor() == "00:11:22"

    @patch('device.netifaces')
    def test_get_default_gw_ip(self, mock_netifaces, device_instance):
        mock_netifaces.gateways.return_value = {'default': {2: ['1.1.1.1']}}
        mock_netifaces.AF_INET = 2
        assert device_instance.get_default_gw_ip() == '1.1.1.1'
        mock_netifaces.gateways.side_effect = Exception("err")
        assert device_instance.get_default_gw_ip() == '127.0.0.1'
        
    @patch('device.requests.get')
    def test_update_dns(self, mock_get, device_instance):
        device_instance.config.has_section.return_value = True
        device_instance.config.getboolean.return_value = True
        mock_get.return_value.status_code = 200
        device_instance.update_dns("1.2.3.4")
        mock_get.assert_called()
        mock_get.return_value.status_code = 202
        mock_get.return_value.content = b'badauth'
        device_instance.update_dns("1.2.3.4")

class TestCommands:
    @patch('device.subprocess.Popen')
    def test_execute_cmd(self, mock_popen, device_instance):
        process_mock = MagicMock()
        process_mock.communicate.return_value = (b'stdout', b'')
        process_mock.wait.return_value = None
        mock_popen.return_value = process_mock
        assert device_instance.execute_cmd("ls") == 0

    def test_reboot(self, device_instance):
        with patch.object(device_instance, 'execute_setuid') as mock_exec:
            device_instance.reboot()
            mock_exec.assert_called_with("1 2")
            device_instance.config.has_option.return_value = True
            device_instance.config.getint.return_value = 1
            device_instance.reboot()
            mock_exec.assert_called_with("1 1")

    def test_services_control(self, device_instance):
        with patch.object(device_instance, 'execute_cmd_output') as mock_exec:
            device_instance.set_sshd_service(True)
            mock_exec.reset_mock()
            device_instance.set_sshd_service(False)
            mock_exec.reset_mock()
            device_instance.set_vnc_service(True)
            device_instance.set_remote_ssh_session(True)
            device_instance.set_remote_ssh_session(False)
            
            # Test exceptions in psutil loops in set_remote_ssh_session
            # Mock psutil to raise exception on attribute access
            with patch('device.psutil') as mock_ps:
                p = MagicMock()
                p.name.side_effect = device.psutil.NoSuchProcess(1)
                mock_ps.process_iter.return_value = [p]
                device_instance.set_remote_ssh_session(False) # Should not raise
            
    @patch('device.psutil')
    def test_is_service_running(self, mock_ps, device_instance):
        p1 = MagicMock()
        p1.name.return_value = "sshd"
        p1.cmdline.return_value = ["/usr/sbin/sshd", "/usr/sbin/sshd"]
        p2 = MagicMock()
        p2.name.return_value = "ssh"
        p2.cmdline.return_value = ["ssh", "remote@relay.we-pn.com"]
        mock_ps.process_iter.return_value = [p1, p2]
        
        assert device_instance.is_ssh_service_running() is True
        assert device_instance.is_remote_session_running() is True
        
        # Exception path
        p1.name.side_effect = device.psutil.NoSuchProcess(1)
        assert device_instance.is_ssh_service_running() is False
    
    @patch('device.Unit')
    def test_is_service_active(self, mock_unit, device_instance):
        unit_instance = mock_unit.return_value
        unit_instance.Unit.ActiveState = b'active'
        assert device_instance.is_service_active('test.service') is True
        unit_instance.Unit.ActiveState = b'inactive'
        assert device_instance.is_service_active('test.service') is False
        
    @patch('device.psutil')
    def test_is_process_running(self, mock_ps, device_instance):
        p = MagicMock()
        p.cmdline.return_value = ["/bin/update-pproxy"]
        mock_ps.process_iter.return_value = [p]
        assert device_instance.is_process_running("update-pproxy") is True
        assert device_instance.is_process_running("not-running") is False
        
    @patch('device.psutil')
    def test_get_process_cmd_by_pid(self, mock_ps, device_instance):
        p = MagicMock()
        p.pid = 123
        p.cmdline.return_value = ["cmd"]
        mock_ps.process_iter.return_value = [p]
        assert device_instance.get_process_cmd_by_pid(123) == ["cmd"]
        assert device_instance.get_process_cmd_by_pid(999) == [""]
    
    def test_mount_operations(self, device_instance):
        with patch.object(device_instance, 'execute_cmd_output') as mock_exec, \
             patch('device.time.sleep'):
             # FIX: mount_drive takes 1 arg (drive)
             device_instance.mount_drive("sda1")
             mock_exec.assert_called()
             device_instance.umount_all_drives()
             mock_exec.assert_called()

class TestUpdate:
    @patch('device.requests.get')
    def test_get_min_ota_version(self, mock_get, device_instance):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"min": "1.0.0"}
        mock_get.return_value = mock_response
        assert device_instance.get_min_ota_version() == "1.0.0"

    def test_wait_for_internet(self, device_instance):
        with patch.object(device_instance, 'get_min_ota_version') as mock_ver, \
             patch('device.time.sleep'):
             mock_ver.return_value = "1.0.0"
             assert device_instance.wait_for_internet() is True
             mock_ver.side_effect = [None, "1.0.0"]
             assert device_instance.wait_for_internet() is True
             mock_ver.side_effect = Exception("fail")
             assert device_instance.wait_for_internet(retries=2) is False

    @patch('device.version')
    def test_needs_package_update(self, mock_version, device_instance):
        mock_version.parse.side_effect = lambda x: int(x.replace('.',''))
        with patch.object(device_instance, 'get_installed_package_version', return_value="0.9.0"), \
             patch.object(device_instance, 'wait_for_internet', return_value=True), \
             patch.object(device_instance, 'is_process_running', return_value=False):
             device_instance.repo_pkg_version = "1.0.0"
             assert device_instance.needs_package_update() is True  

    @patch('device.requests.get')
    def test_get_repo_package_version(self, mock_get, device_instance):
        mock_get.return_value.text = "Package: pproxy\nVersion: 1.2.3\n"
        with patch('device.platform.architecture', return_value=('64bit', '')), \
             patch('device.distro.codename', return_value='bookworm'):
             assert device_instance.get_repo_package_version() == "1.2.3"
        mock_get.side_effect = Exception("error")
        assert device_instance.get_repo_package_version() is None
        mock_get.side_effect = device.requests.exceptions.ConnectionError("conn error")
        assert device_instance.get_repo_package_version() is None

    def test_get_installed_package_version(self, device_instance):
        with patch.object(device_instance, 'execute_cmd_output') as mock_exec:
            # Code uses str(result[0]).split("\\n"). 
            # If we return bytes, str(b'...') gives "b'...'" which has \\n rep.
            # But simpler to return string if we want ensuring match, UNLESS code relies on bytes-str conversion artifact?
            # Code: str(result[0]).
            # If I return "output", str("output") is "output".
            mock_exec.return_value = ("ii  pproxy-rpi  1.0.0  armhf  desc", "", 0, None)
            assert device_instance.get_installed_package_version() == "1.0.0"
            
            mock_exec.return_value = ("nothing here", "", 0, None)
            assert device_instance.get_installed_package_version() == "0.0.0"
            
    def test_software_update_blocking(self, device_instance):
        with patch.object(device_instance, 'needs_package_update', side_effect=[True, False]), \
             patch.object(device_instance, 'execute_setuid') as mock_exec, \
             patch('device.time.sleep'):
            lcd = MagicMock()
            leds = MagicMock()
            device_instance.software_update_blocking(lcd, leds)
            mock_exec.assert_called_with("1 3")

    def test_software_update_from_git(self, device_instance):
         with patch.object(device_instance, 'execute_cmd_output') as mock_exec:
             device_instance.software_update_from_git()
             assert mock_exec.call_count == 2

    def test_switch_ota_channel(self, device_instance):
        device_instance.status.get_field.return_value = "prod"
        with patch.object(device_instance, 'execute_cmd_output') as mock_exec:
            assert device_instance.switch_ota_channel("prod") is False
            assert device_instance.switch_ota_channel("beta") is True
        
        with patch.object(device_instance, 'execute_cmd_output') as mock_exec:
             device_instance.switch_ota_channel("prod")

class TestConfig:
    def test_is_config_populated(self, device_instance):
         with patch('device.configparser.ConfigParser') as mock_cp:
            mock_cp.return_value.get.return_value = "val"
            assert device_instance.is_config_populated() is True
    
    def test_config_matches_serial(self, device_instance):
        with patch('device.configparser.ConfigParser') as mock_cp:
            mock_cp.return_value.get.return_value = "SN123"
            assert device_instance.config_matches_serial("path", "SN123") is True
            assert device_instance.config_matches_serial("path", "SN999") is False
            
            with patch('device.os.path.isfile', return_value=False):
                assert device_instance.config_matches_serial("bad/path", "SN123") is True

    def test_generate_new_config(self, device_instance):
        with patch('device.os.path.isfile') as mock_isfile, \
             patch('device.os.path.isdir') as mock_isdir, \
             patch('builtins.open', mock_open(read_data="config")) as mock_file, \
             patch.object(device_instance, 'umount_all_drives'), \
             patch.object(device_instance, 'mount_drive'), \
             patch.object(device_instance, 'config_matches_serial', return_value=True):
             
             mock_isfile.side_effect = lambda x: x == "/mnt/wepn.ini"
             assert device_instance.generate_new_config() is True
             
             mock_isfile.side_effect = lambda x: x == "/mnt/etc/pproxy/config.ini"
             assert device_instance.generate_new_config() is True
             
             # Priority 3
             mock_isfile.return_value = False
             mock_isdir.return_value = True
             mock_setup = MagicMock()
             mock_setup.create_config.return_value = "config"
             with patch.dict('sys.modules', {'setup_mod': mock_setup}):
                 assert device_instance.generate_new_config() is True

    @patch('device.requests.get')
    def test_get_device_config_backend(self, mock_get, device_instance):
        mock_get.return_value.json.return_value = {"key": "val"}
        res = device_instance.get_device_config_backend()
        assert res == {"key": "val"}

    @patch('builtins.open', new_callable=mock_open)
    def test_fetch_config_from_backend(self, mock_file, device_instance):
        with patch.object(device_instance, 'is_config_populated', return_value=False):
            with patch('device.requests.get') as mock_req:
                 mock_req.return_value.status_code = 200
                 mock_req.return_value.content = b"new_config"
                 assert device_instance.fetch_config_from_backend() is True
                 mock_req.reset_mock()
                 mock_req.side_effect = Exception("error")
                 assert device_instance.fetch_config_from_backend() is False

    @patch('builtins.open', new_callable=mock_open)
    def test_get_error_logs(self, mock_file, device_instance):
        mock_file.side_effect = [
            mock_open(read_data="log1").return_value,
            mock_open(read_data="log2").return_value
        ]
        logs = device_instance.get_error_logs()
        assert logs == "log1log2"
        
        # Exception path
        mock_file.reset_mock()
        mock_file.side_effect = FileNotFoundError 
        logs = device_instance.get_error_logs()
        assert logs == ""

class TestSystem:
    @patch('device.psutil')
    @patch('device.shutil.disk_usage')
    def test_get_system_health_stats(self, mock_du, mock_psutil, device_instance):
        mock_psutil.virtual_memory.return_value.available = 1000
        mock_du.return_value.total = 1000
        mock_du.return_value.used = 500
        with patch('device.distro.codename', return_value='bookworm'):
             stats = device_instance.get_system_health_stats()
             assert stats['os'] != "unknown"
        
        # Exception path: returns valid dict with partial data
        mock_psutil.virtual_memory.side_effect = Exception("err")
        stats = device_instance.get_system_health_stats()
        assert stats.get('ram_total') is None 
        assert 'hd' in stats
        
        # Disk Usage Exception path
        mock_psutil.virtual_memory.side_effect = None
        mock_du.side_effect = Exception("disk err")
        stats = device_instance.get_system_health_stats()
        assert stats['hd'] == 0
        
        # Distro/Arch exceptions
        with patch('device.distro.codename', side_effect=Exception("err")), \
             patch('device.platform.architecture', return_value=('64bit', '')):
             stats = device_instance.get_system_health_stats()
             # dist="unknown", arch="arm64" -> "unknown-arm64"
             assert stats['os'] == "unknown-arm64"
             
        with patch('device.platform.architecture', side_effect=Exception("err")), \
             patch('device.distro.codename', return_value='bookworm'):
             stats = device_instance.get_system_health_stats()
             # dist="bookworm", arch="unknown" -> "bookworm-unknown"
             assert stats['os'] == "bookworm-unknown"
             
        # Arch armhf path
        with patch('device.platform.architecture', return_value=('32bit', '')), \
             patch('device.distro.codename', return_value='bookworm'):
             stats = device_instance.get_system_health_stats()
             assert stats['os'] == "bookworm-armhf"

    def test_is_legacy_gpio(self, device_instance):
        with patch('device.platform.release', return_value="5.4.0-rpi"), \
             patch('device.Version') as mock_ver:
             mock_ver.side_effect = lambda x: int(x.split('.')[0])
             assert device_instance.is_legacy_gpio() is True
             
             # False Case
             mock_ver.side_effect = lambda x: int(x.split('.')[0])
             with patch('device.platform.release', return_value="6.0.0-rpi"):
                 assert device_instance.is_legacy_gpio() is False
            
    def test_get_os_info(self, device_instance):
        with patch('builtins.open', mock_open(read_data='VERSION_CODENAME=bookworm\nID=debian')):
             info = device_instance.get_os_info()
             assert info['VERSION_CODENAME'].strip() == 'bookworm'
             assert device_instance.get_os_codename() == 'bookworm\n' 
