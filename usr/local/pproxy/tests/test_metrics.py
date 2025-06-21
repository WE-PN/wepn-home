import json
import logging.config
import logging
import os
import sys
import time
up_dir = os.path.dirname(os.path.abspath(__file__)) + '/../'
sys.path.append(up_dir)

from metrics_client import MetricsClient
from constants import METRICS_PORT

SERVER_HOST = '127.0.0.1' # Use '127.0.0.1' or 'localhost' if running on the same machine

client = MetricsClient(SERVER_HOST, METRICS_PORT)

try:
    # 1. Add some ports to monitor
    print("\n--- Adding ports 80, 443, 22 ---")
    add_response = client.add_ports([80, 443, 22])
    print(f"Server response: {add_response.get('message')}")

    # 2. List currently monitored ports
    print("\n--- Listing monitored ports ---")
    monitored_ports = client.list_ports()
    print(f"Currently monitored ports: {monitored_ports}")

    # Give the server some time to detect connections
    print("\n--- Waiting 10 seconds for connections to accumulate ---")
    time.sleep(10)

    # 3. Get a report of connections
    print("\n--- Getting first report ---")
    report = client.get_report()
    print(json.dumps(report, indent=2))

    print("\n--- Waiting 5 seconds and getting a second report (should show new connections since last report) ---")
    time.sleep(10)
    second_report = client.get_report()
    print(json.dumps(second_report, indent=2))
    
    # 4. Add another port
    print("\n--- Adding port 8080 ---")
    add_another_response = client.add_ports([8080])
    print(f"Server response: {add_another_response.get('message')}")

    # 5. Get report again (now including any connections on 8080)
    print("\n--- Getting report after adding 8080 ---")
    time.sleep(15)
    report_after_add = client.get_overall_report()
    print(json.dumps(report_after_add, indent=2))

    # 6. Remove a port
    print("\n--- Removing port 22 ---")
    remove_response = client.remove_ports([22])
    print(f"Server response: {remove_response.get('message')}")

    # 7. List ports again to confirm removal
    print("\n--- Listing monitored ports after removal ---")
    monitored_ports_after_remove = client.list_ports()
    print(f"Currently monitored ports: {monitored_ports_after_remove}")

except IOError as e:
    print(f"Client connection error: {e}")
except ValueError as e:
    print(f"Client logic error: {e}")
except json.JSONDecodeError as e:
    print(f"JSON parsing error: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")

print("\nClient example finished.")
