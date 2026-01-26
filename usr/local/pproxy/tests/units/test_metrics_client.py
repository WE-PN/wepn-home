import pytest
import json
import socket
from unittest.mock import MagicMock, patch, mock_open
from metrics_client import MetricsClient


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_socket():
    with patch('socket.socket') as m:
        mock_instance = MagicMock()
        m.return_value.__enter__.return_value = mock_instance
        yield mock_instance


def test_init_defaults():
    with patch('logging.config.fileConfig'), patch('logging.getLogger') as mock_get_logger:
        client = MetricsClient()
        assert client.host == '127.0.0.1'
        assert client.port == 8411  # from constants.py
        mock_get_logger.assert_called_with("metrics_client")


def test_init_custom(mock_logger):
    client = MetricsClient(host='1.2.3.4', port=9999, logger=mock_logger)
    assert client.host == '1.2.3.4'
    assert client.port == 9999
    assert client.logger == mock_logger


def test_send_command_success(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.recv.side_effect = [b'{"status": "ok", "message": "done"}\n', b'']

    response = client._send_command({"cmd": "test"})
    assert response == {"status": "ok", "message": "done"}
    mock_socket.connect.assert_called_with(('127.0.0.1', 8411))
    mock_socket.sendall.assert_called()


def test_send_command_server_error(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.recv.side_effect = [b'{"status": "error", "message": "fail"}\n', b'']

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "Server error: fail" in response["error"]


def test_send_command_timeout(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.connect.side_effect = socket.timeout("timed out")

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "timed out" in response["error"]


def test_send_command_connection_refused(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.connect.side_effect = ConnectionRefusedError()

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "Could not connect to server" in response["error"]


def test_send_command_socket_error(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.connect.side_effect = socket.error("boom")

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "Network error: boom" in response["error"]


def test_send_command_json_error(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.recv.side_effect = [b'invalid json\n', b'']

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "Invalid JSON" in response["error"]


def test_send_command_empty_response(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.recv.return_value = b''

    response = client._send_command({"cmd": "test"})
    assert response == {"error": "empty response"}


def test_send_command_unexpected_exception(mock_logger, mock_socket):
    client = MetricsClient(logger=mock_logger)
    mock_socket.connect.side_effect = Exception("wild error")

    response = client._send_command({"cmd": "test"})
    assert "error" in response
    assert "An unexpected error occurred" in response["error"]


def test_add_ports(mock_logger):
    client = MetricsClient(logger=mock_logger)
    ports = [{"port": 80}, {"port": 443}]

    with patch.object(client, '_send_command', return_value={"status": "ok"}) as mock_send:
        res = client.add_ports(ports)
        assert res == {"status": "ok"}
        mock_send.assert_called_with({"command": "add_ports", "ports": ports})


def test_add_ports_invalid(mock_logger):
    client = MetricsClient(logger=mock_logger)
    with pytest.raises(ValueError, match="Ports must be a list of integers."):
        client.add_ports([80, 443])  # Code expects dicts, though doc says ints


def test_remove_ports(mock_logger):
    client = MetricsClient(logger=mock_logger)
    ports = [80, 443]

    with patch.object(client, '_send_command', return_value={"status": "ok"}) as mock_send:
        res = client.remove_ports(ports)
        assert res == {"status": "ok"}
        mock_send.assert_called_with({"command": "remove_ports", "ports": ports})


def test_remove_ports_invalid(mock_logger):
    client = MetricsClient(logger=mock_logger)
    with pytest.raises(ValueError, match="Ports must be a list of integers."):
        client.remove_ports([{"port": 80}])


def test_list_ports(mock_logger):
    client = MetricsClient(logger=mock_logger)
    with patch.object(client, '_send_command', return_value={"monitored_ports": [80, 443]}) as mock_send:
        res = client.list_ports()
        assert res == [80, 443]


def test_get_report(mock_logger):
    client = MetricsClient(logger=mock_logger)
    report_data = {"80": 100, "443": 200}
    with patch.object(client, '_send_command', return_value={"data": report_data}) as mock_send:
        res = client.get_report()
        assert res == report_data


def test_get_overall_report(mock_logger):
    client = MetricsClient(logger=mock_logger)
    report_data = {"total_connections": 1000}
    with patch.object(client, '_send_command', return_value={"data": report_data}) as mock_send:
        res = client.get_overall_report()
        assert res == report_data
