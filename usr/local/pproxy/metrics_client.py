import json
import logging
from logging import config # noqa
import socket

from constants import LOG_CONFIG
logging.config.fileConfig(LOG_CONFIG,
                          disable_existing_loggers=False)


class MetricsClient:
    """
    A simple client library to interact with the Metrics Server.

    Provides methods to:
    - Add ports to monitor
    - Remove ports from monitoring
    - List currently monitored ports
    - Get a report of observed connections
    """
    def __init__(self, host: str, port: int, logger=None):
        """
        Initializes the client with the server's address.

        Args:
            host (str): The hostname or IP address of the monitoring server.
            port (int): The port number the monitoring server is listening on.
            logger (Python Logger): A default logger, if caller wants to capture logs
        """
        self.host = host
        self.port = port
        if logger is not None:
            self.logger = logger
        else:
            self.logger = logging.getLogger("metrics_client")

    def _send_command(self, command_dict: dict) -> dict:
        """
        Internal helper to send a JSON command to the server and receive its response.

        Args:
            command_dict (dict): The command to send as a Python dictionary.

        Returns:
            dict: The parsed JSON response from the server.

        Raises:
            IOError: If there's a network communication error.
            json.JSONDecodeError: If the server sends invalid JSON.
            ValueError: If the server's response indicates an error.
        """
        self.logger.debug(f"Sending command: {json.dumps(command_dict)}")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                sock.connect((self.host, self.port))

                # Send the JSON command encoded as bytes
                json_command = json.dumps(command_dict).encode('utf-8')
                sock.sendall(json_command)

                # Receive the response
                # Accumulate data until no more is received (or timeout)
                response_buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response_buffer += chunk
                    # Simple check for newline to assume end of JSON if streaming,
                    # but typically server sends all at once for small responses.
                    if b'\n' in chunk:
                        break

                json_response_str = response_buffer.decode('utf-8').strip()
                self.logger.debug(f"Received raw response: {json_response_str}")

                if not json_response_str:
                    raise IOError("Received empty response from server.")

                # Parse the JSON response
                response_data = json.loads(json_response_str)

                # Check for server-side errors
                if response_data.get("status") == "error":
                    raise ValueError(f"Server error: {response_data.get('message', 'Unknown server error')}")

                return response_data

        except socket.timeout as e:
            self.logger.error(f"Socket timeout during communication: {e}")
            raise IOError(f"Connection timed out: {e}") from e
        except ConnectionRefusedError:
            self.logger.error(f"Connection refused by server at {self.host}:{self.port}")
            raise IOError(f"Could not connect to server at {self.host}:{self.port}. Is it running?")
        except socket.error as e:
            self.logger.error(f"Socket error during communication: {e}")
            raise IOError(f"Network error: {e}") from e
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON received from server: {e}\nRaw: {json_response_str}")
            raise json.JSONDecodeError(f"Invalid JSON response: {e}", e.doc, e.pos) from e
        except Exception as e:
            self.logger.error(f"An unexpected error occurred in _send_command: {e}", exc_info=True)
            raise

    def add_ports(self, ports: list) -> dict:
        """
        Sends a command to the server to add ports to monitor.

        Args:
            ports (list): A list of integer port numbers to add.

        Returns:
            dict: The server's success response message.
        """
        if not isinstance(ports, list) or not all(isinstance(p, int) for p in ports):
            raise ValueError("Ports must be a list of integers.")
        command = {"command": "add_ports", "ports": ports}

        self.logger.info(f"Requesting to add ports: {ports}")
        return self._send_command(command)

    def remove_ports(self, ports: list) -> dict:
        """
        Sends a command to the server to remove ports from monitoring.

        Args:
            ports (list): A list of integer port numbers to remove.

        Returns:
            dict: The server's success response message.
        """
        if not isinstance(ports, list) or not all(isinstance(p, int) for p in ports):
            raise ValueError("Ports must be a list of integers.")

        command = {"command": "remove_ports", "ports": ports}
        self.logger.info(f"Requesting to remove ports: {ports}")
        return self._send_command(command)

    def list_ports(self) -> list:
        """
        Sends a command to the server to get a list of currently monitored ports.

        Returns:
            list: A sorted list of integer port numbers being monitored.
        """
        command = {"command": "list_ports"}
        self.logger.info("Requesting list of monitored ports.")
        response = self._send_command(command)
        return response.get("monitored_ports", [])

    def get_report(self) -> dict:
        """
        Sends a command to the server to get the accumulated connections report.

        Returns:
            dict: The report data from the server.
        """
        command = {"command": "get_report"}
        self.logger.info("Requesting connections report.")
        response = self._send_command(command)
        return response.get("data", {})

    def get_overall_report(self) -> dict:
        """
        Sends a command to the server to get the overall connections report.

        Returns:
            dict: The report data from the server.
        """
        command = {"command": "get_overall_report"}
        self.logger.info("Requesting connections report.")
        response = self._send_command(command)
        return response.get("data", {})
