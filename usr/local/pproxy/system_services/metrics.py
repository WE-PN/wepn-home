import psutil
import socket
import maxminddb
import time
import os
import sys
import threading
import json
import logging
from logging import config # noqa

up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from constants import METRICS_PORT # noqa

# --- Configuration for MaxMind DBs ---
BASE_DIR = '/var/local/pproxy/geolocation/'
ASN_DB_PATH = BASE_DIR + 'asn.mmdb'
COUNTRY_DB_PATH = BASE_DIR + 'country.mmdb'

# --- Monitoring and Reporting Configuration ---
CHECK_INTERVAL_SECONDS = 5    # How often the background thread checks connections
LISTENING_HOST = '127.0.0.1'    # Listen on all available network interfaces

# --- Logger Configuration ---
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)
from constants import LOG_CONFIG  # noqa E402 need up_dir first
logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)
logger = logging.getLogger(__name__)

# Global shared data and lock
# _ports_to_monitor: Set of ports currently being monitored. Dynamically updated.
_ports_to_monitor = set()

# _connections_for_next_report_per_port: { port_num: set( (ip, asn, country), ... ), ... }
# This set accumulates all unique connections observed as active within the current reporting cycle
# (i.e., since the last client query). It gets cleared after each report.
_connections_for_next_report_per_port = {}

# _all_time_observed_asns: set of all unique ASNs ever seen since script started.
_all_time_observed_asns = set()
# _all_time_observed_countries: set of all unique countries ever seen since script started.
_all_time_observed_countries = set()

_data_lock = threading.Lock()  # Lock to protect access to shared global sets

# --- Connection checking function (returns structured snapshot data) ---


def get_current_connections_snapshot(ports_to_check, geoip_asn_db_path, geoip_country_db_path=None):
    """
    Checks for remote IP addresses connected to a given list of TCP/UDP ports,
    and returns a snapshot of active connections with their ASNs and country names.
    Uses "UNKNOWN-{first_octet}" if ASN/Country is not found.
    """
    snapshot_connections_per_port = {port: set() for port in ports_to_check}
    snapshot_all_asns = set()
    snapshot_all_countries = set()

    if not ports_to_check:
        return {}, set(), set()

    asn_reader = None
    country_reader = None

    try:
        asn_reader = maxminddb.open_database(geoip_asn_db_path)
    except maxminddb.InvalidDatabaseError:
        logger.error(f"Could not open AS/Primary MaxMind DB at '{geoip_asn_db_path}'. "
                     "Please ensure the path is correct and the file is a valid MMDB.")
        return {}, set(), set()
    except FileNotFoundError:
        logger.error(f"AS/Primary MaxMind DB file not found at '{geoip_asn_db_path}'. "
                     "Please download ASN.mmdb (or Country.mmdb if preferred) and place it correctly.")
        return {}, set(), set()

    if geoip_country_db_path and os.path.exists(geoip_country_db_path):
        try:
            country_reader = maxminddb.open_database(geoip_country_db_path)
        except (maxminddb.InvalidDatabaseError, FileNotFoundError) as e:
            logger.warning(f"Could not load country DB from '{geoip_country_db_path}': {e}. "
                           "Will attempt to get country from the primary AS DB instead.")
            country_reader = asn_reader
    else:
        if geoip_country_db_path:
            logger.warning(f"Country DB path '{geoip_country_db_path}' not found. "
                           "Will attempt to get country from the primary AS DB instead.")
        country_reader = asn_reader

    for conn in psutil.net_connections(kind='inet'):
        if conn.laddr and conn.raddr:
            local_port = conn.laddr.port
            remote_ip = conn.raddr.ip

            if local_port in ports_to_check:
                asn = None
                country_name = None

                # Determine IP prefix for UNKNOWN strings
                ip_prefix = "UNKNOWN"
                # Check for IPv6 vs IPv4 to correctly extract "first octet" equivalent
                if ':' in remote_ip:  # Likely IPv6
                    ip_prefix += "-IPv6"
                elif '.' in remote_ip:  # Likely IPv4
                    try:
                        first_octet = remote_ip.split('.')[0]
                        if first_octet.isdigit():  # Basic check if it looks like an octet
                            ip_prefix = f"UNKNOWN-{first_octet}"
                        else:  # Malformed or unexpected IPv4 segment
                            ip_prefix = "UNKNOWN-InvalidIPv4"
                    except IndexError:  # IP string empty or malformed
                        ip_prefix = "UNKNOWN-MalformedIP"
                else:  # Neither IPv4 nor IPv6 format
                    ip_prefix = "UNKNOWN-OtherIP"

                try:
                    asn_record = asn_reader.get(remote_ip)
                    if asn_record and 'autonomous_system_number' in asn_record:
                        asn = asn_record['autonomous_system_number']
                    else:
                        asn = ip_prefix  # Set to UNKNOWN if not found in record
                except Exception as e:
                    logger.debug(f"Error getting ASN for {remote_ip}: {e}")
                    asn = ip_prefix  # Set to UNKNOWN on lookup error

                try:
                    country_record = country_reader.get(remote_ip)
                    if country_record:
                        if 'country' in country_record and 'names' in country_record['country'] and 'en' in country_record['country']['names']:
                            country_name = country_record['country']['names']['en']
                        elif 'registered_country' in country_record and 'names' in country_record['registered_country'] and 'en' in country_record['registered_country']['names']:
                            country_name = country_record['registered_country']['names']['en']
                        else:  # Set to UNKNOWN if record existed but no country info
                            country_name = ip_prefix
                    else:  # Set to UNKNOWN if no record for IP at all
                        country_name = ip_prefix
                except Exception as e:
                    logger.debug(f"Error getting Country for {remote_ip}: {e}")
                    country_name = ip_prefix  # Set to UNKNOWN on error

                snapshot_connections_per_port[local_port].add((remote_ip, asn, country_name))

                # Still add to overall observed sets only if they are valid numbers/names
                # and not the "UNKNOWN" placeholder strings
                if isinstance(asn, int) or (isinstance(asn, str) and not str(asn).startswith("UNKNOWN")):
                    snapshot_all_asns.add(asn)
                if isinstance(country_name, str) and not country_name.startswith("UNKNOWN"):
                    snapshot_all_countries.add(country_name)

    if asn_reader:
        asn_reader.close()
    if country_reader and country_reader is not asn_reader:
        country_reader.close()

    return snapshot_connections_per_port, snapshot_all_asns, snapshot_all_countries


# --- Thread function for continuous monitoring ---
def monitor_connections(asn_db, country_db, check_interval):
    """
    This function runs in a separate thread to continuously monitor connections
    and update global shared data structures.
    """
    global _ports_to_monitor
    global _connections_for_next_report_per_port, _all_time_observed_asns, _all_time_observed_countries
    global _data_lock

    logger.info(f"Monitoring thread started. Checking connections every {check_interval} seconds.")
    try:
        while True:
            with _data_lock:
                current_monitored_ports = list(_ports_to_monitor)
                for port in current_monitored_ports:
                    if port not in _connections_for_next_report_per_port:
                        _connections_for_next_report_per_port[port] = set()

            current_snapshot_connections_per_port, current_snapshot_asns, current_snapshot_countries = \
                get_current_connections_snapshot(current_monitored_ports, asn_db, country_db)

            with _data_lock:  # Acquire lock to safely update shared global data
                for port, connections_set in current_snapshot_connections_per_port.items():
                    _connections_for_next_report_per_port[port].update(connections_set)

                _all_time_observed_asns.update(current_snapshot_asns)
                _all_time_observed_countries.update(current_snapshot_countries)

            time.sleep(check_interval)  # Pause before the next check
    except Exception as e:
        logger.error(f"Monitoring thread encountered an error and stopped: {e}", exc_info=True)


# --- Main function for listening and processing client commands ---
if __name__ == "__main__":
    logger.info("Script started. Monitoring connections in the background.")
    logger.info(
        "It dynamically manages monitored ports and reports observed connections via a listening JSON API.")
    logger.info("Please ensure you have 'maxminddb-python' installed and the  MMDB files downloaded.")
    logger.info(
        "You will need at least 'ASN.mmdb'. Optionally, 'Country.mmdb' for dedicated country lookup.")

    # --- Start the background monitoring thread ---
    monitor_thread = threading.Thread(target=monitor_connections,
                                      args=(ASN_DB_PATH, COUNTRY_DB_PATH, CHECK_INTERVAL_SECONDS),
                                      daemon=True)  # Daemon means thread will exit automatically when main program exits
    monitor_thread.start()

    # --- Set up the listening socket for client commands/reports ---
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,
                             1)  # Allows immediate reuse of the address

    try:
        server_socket.bind((LISTENING_HOST, METRICS_PORT))
        server_socket.listen(5)  # Allow up to 5 queued client connections
        logger.info(
            f"Listening for client commands and JSON reports on {LISTENING_HOST}:{METRICS_PORT}.")
        logger.info("Commands (send as JSON to this port):")
        logger.info("  - Add ports:    {\"command\": \"add_ports\",    \"ports\": [80, 443]}")
        logger.info("  - Remove ports: {\"command\": \"remove_ports\", \"ports\": [22]}")
        logger.info("  - List ports:   {\"command\": \"list_ports\"}")
        logger.info("  - Get report:   {\"command\": \"get_report\"}")
        logger.info("Press Ctrl+C to stop the script.")

        while True:
            conn, addr = server_socket.accept()  # This call blocks until a client connects
            logger.info(f"Client connected from {addr}. Processing command...")

            try:
                # Receive data from client (assuming a single JSON message per connection)
                # Max 4096 bytes, adjust buffer size as needed
                data = conn.recv(4096).decode('utf-8')
                command_data = json.loads(data)

                command = command_data.get("command")
                response = {"status": "error", "message": "Invalid command or parameters."}

                if command == "add_ports":
                    ports_to_manage = command_data.get("ports")
                    if isinstance(ports_to_manage, list) and all(isinstance(p, int) for p in ports_to_manage):
                        with _data_lock:
                            initial_count = len(_ports_to_monitor)
                            _ports_to_monitor.update(ports_to_manage)
                            for p in ports_to_manage:
                                if p not in _connections_for_next_report_per_port:
                                    _connections_for_next_report_per_port[p] = set()
                            added_count = len(_ports_to_monitor) - initial_count
                        response = {
                            "status": "success", "message": f"Added {added_count} new ports. Currently monitoring: {sorted(list(_ports_to_monitor))}"}
                        logger.info(f"Updated monitored ports: {sorted(list(_ports_to_monitor))}")
                    else:
                        response = {"status": "error",
                                    "message": "'ports' must be a list of integers."}

                elif command == "remove_ports":
                    ports_to_manage = command_data.get("ports")
                    if isinstance(ports_to_manage, list) and all(isinstance(p, int) for p in ports_to_manage):
                        with _data_lock:
                            initial_count = len(_ports_to_monitor)
                            _ports_to_monitor.difference_update(ports_to_manage)
                            for p in ports_to_manage:
                                if p in _connections_for_next_report_per_port:
                                    del _connections_for_next_report_per_port[p]
                            removed_count = initial_count - len(_ports_to_monitor)
                        response = {
                            "status": "success", "message": f"Removed {removed_count} ports. Currently monitoring: {sorted(list(_ports_to_monitor))}"}
                        logger.info(f"Updated monitored ports: {sorted(list(_ports_to_monitor))}")
                    else:
                        response = {"status": "error",
                                    "message": "'ports' must be a list of integers."}

                elif command == "list_ports":
                    with _data_lock:
                        current_ports = sorted(list(_ports_to_monitor))
                    response = {"status": "success", "monitored_ports": current_ports}

                elif command == "get_overall_report":
                    report_data = {
                        "ts": int(time.time()),
                        "overall": {
                            "as_numbers": [],
                            "countries": []
                        }
                    }

                    with _data_lock:
                        report_data["overall"]["as_numbers"] = sorted(list(_all_time_observed_asns))
                        report_data["overall"]["countries"] = sorted(
                            list(_all_time_observed_countries))

                    response = {"status": "success", "data": report_data}

                elif command == "get_report":
                    report_data = {
                        "ts": int(time.time()),
                        "connections": {},
                    }

                    with _data_lock:
                        for port in sorted(list(_ports_to_monitor)):
                            if port in _connections_for_next_report_per_port:
                                connections_for_report = []
                                for ip, asn, country in sorted(list(_connections_for_next_report_per_port[port])):
                                    connections_for_report.append({
                                        "asn": asn,
                                        "country": country
                                    })

                                if connections_for_report:
                                    report_data["connections"][f"{port}"] = connections_for_report

                                _connections_for_next_report_per_port[port].clear()
                                if port not in _connections_for_next_report_per_port:
                                    _connections_for_next_report_per_port[port] = set()

                    response = {"status": "success", "data": report_data}

                else:
                    response = {"status": "error", "message": "Unknown command."}

                json_response = json.dumps(response, indent=2) + "\n"
                conn.sendall(json_response.encode('utf-8'))
                logger.info(f"Response sent to {addr}. Size: {len(json_response)} bytes.")

            except json.JSONDecodeError:
                error_response = json.dumps(
                    {"status": "error", "message": "Invalid JSON format. Please send valid JSON."}) + "\n"
                conn.sendall(error_response.encode('utf-8'))
                logger.warning(f"Invalid JSON received from {addr}.")
            except Exception as e:
                error_response = json.dumps(
                    {"status": "error", "message": f"Server error: {e}"}) + "\n"
                conn.sendall(error_response.encode('utf-8'))
                logger.error(f"Error processing client request from {addr}: {e}", exc_info=True)
            finally:
                conn.close()

    except KeyboardInterrupt:
        logger.info("Monitoring and reporting stopped by user (Ctrl+C).")
    except Exception as e:
        logger.critical(
            f"An unexpected critical error occurred in the main listening thread: {e}", exc_info=True)
    finally:
        server_socket.close()
        logger.info("Script terminated.")
