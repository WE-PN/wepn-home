import pytest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os
import base64
import json

# Add the parent directory to sys.path to import messages
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock logging config before importing messages to avoid side effects
with patch('logging.config.fileConfig'):
    from messages import Messages

@pytest.fixture
def mock_config():
    with patch('configparser.ConfigParser') as mock_parser:
        parser_instance = mock_parser.return_value
        # Default config values
        parser_instance.get.side_effect = lambda section, option: {
            ('django', 'url'): 'http://mock-url',
            ('django', 'serial_number'): 'mock-serial',
            ('django', 'device_key'): 'mock-device-key',
            ('status', 'e2e_key'): base64.urlsafe_b64encode(b'0'*32).decode('utf-8')
        }.get((section, option))
        
        parser_instance.has_option.side_effect = lambda section, option: \
            section == 'status' and option == 'e2e_key'
            
        yield parser_instance

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def messages_instance(mock_config, mock_logger):
    with patch('messages.CONFIG_FILE', 'mock_config.ini'), \
         patch('messages.STATUS_FILE', 'mock_status.ini'):
        return Messages(logger=mock_logger)

def test_init(mock_config, mock_logger):
    with patch('messages.CONFIG_FILE', 'mock_config.ini'), \
         patch('messages.STATUS_FILE', 'mock_status.ini'):
        msgs = Messages(logger=mock_logger)
        assert msgs.logger == mock_logger
        assert msgs.config.read.called
        assert msgs.status.read.called

def test_init_no_logger(mock_config):
    with patch('messages.CONFIG_FILE', 'mock_config.ini'), \
         patch('messages.STATUS_FILE', 'mock_status.ini'), \
         patch('logging.getLogger') as mock_get_logger:
        msgs = Messages()
        mock_get_logger.assert_called_with("messages")
        assert msgs.logger == mock_get_logger.return_value

def test_e2ee_available(messages_instance):
    assert messages_instance.e2ee_available() is True
    
    # Test when key is missing
    messages_instance.status.has_option.side_effect = lambda s, o: False
    assert messages_instance.e2ee_available() is False

@patch('requests.get')
def test_get_messages_plain(mock_get, messages_instance):
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "id": 1,
            "message_body": {
                "is_secure": False,
                "message": "hello world"
            }
        }
    ]
    mock_get.return_value = mock_response

    msgs = messages_instance.get_messages()
    
    assert len(msgs) == 1
    assert msgs[0]["message_body"]["message"] == "hello world"
    assert 1 in messages_instance.pending_items

@patch('requests.get')
def test_get_messages_secure(mock_get, messages_instance):
    # Prepare a real encrypted message to test decryption
    plain_text = "secret message"
    encrypted, nonce = messages_instance.encrypt_message(plain_text)
    
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "id": 2,
            "message_body": {
                "is_secure": True,
                "nonce": base64.urlsafe_b64encode(nonce).decode('utf-8'),
                "message": base64.urlsafe_b64encode(encrypted).decode('utf-8')
            }
        }
    ]
    mock_get.return_value = mock_response

    msgs = messages_instance.get_messages()
    
    assert len(msgs) == 1
    assert msgs[0]["message_body"]["decrypted"] == plain_text

@patch('requests.get')
def test_get_messages_decryption_failure(mock_get, messages_instance):
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "id": 3,
            "message_body": {
                "is_secure": True,
                "nonce": "bad_nonce",
                "message": "bad_message"
            }
        }
    ]
    mock_get.return_value = mock_response

    # Should not raise exception, but catch it and print error
    msgs = messages_instance.get_messages()
    assert len(msgs) == 1
    assert "decrypted" not in msgs[0]["message_body"]

@patch('requests.get')
def test_get_messages_key_error(mock_get, messages_instance):
    mock_response = MagicMock()
    # Missing 'message_body' but structure implies it might be there or similar malformed data
    # The code does: if "message_body" in msg and "is_secure" in msg["message_body"]:
    # To trigger the KeyError inside the try block, we need to enter the if, then fail.
    # But the if checks for existence.
    # Wait, line 53 checks: if "message_body" in msg and "is_secure" in msg["message_body"]:
    # If that passes, it goes to line 54.
    # Line 56: msg["message_body"]["nonce"]
    # If "nonce" is missing, it raises KeyError.
    
    mock_response.json.return_value = [
        {
            "id": 4,
            "message_body": {
                "is_secure": True,
                # nonce is missing
                "message": "some message"
            }
        }
    ]
    mock_get.return_value = mock_response

    # Capture stdout to verify print
    with patch('builtins.print') as mock_print:
        msgs = messages_instance.get_messages()
        assert len(msgs) == 1
        # Verify print was called with "key not found"
        # The code prints: "key not found:" + str(e)
        assert mock_print.called
        assert "key not found" in mock_print.call_args[0][0]

@patch('requests.patch')
def test_mark_msg_read_success(mock_patch, messages_instance):
    messages_instance.pending_items.append(123)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_patch.return_value = mock_response

    messages_instance.mark_msg_read(123)
    
    assert 123 not in messages_instance.pending_items

@patch('requests.patch')
def test_mark_msg_read_failure(mock_patch, messages_instance):
    messages_instance.pending_items.append(456)
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_patch.return_value = mock_response

    messages_instance.mark_msg_read(456)
    
    assert 456 not in messages_instance.pending_items
    messages_instance.logger.critical.assert_called()

@patch('requests.patch')
def test_mark_msg_read_failure_not_pending(mock_patch, messages_instance):
    # Item 789 is NOT in pending_items
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_patch.return_value = mock_response

    messages_instance.mark_msg_read(789)
    
    # Should log critical error about not being pending
    # "Message " + str(id) + "was marked as read, but it was not pending"
    call_args_list = messages_instance.logger.critical.call_args_list
    found = False
    for args, _ in call_args_list:
        if "was marked as read, but it was not pending" in args[0]:
            found = True
            break
    assert found

@patch('requests.post')
def test_send_msg_plain(mock_post, messages_instance):
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_post.return_value = mock_response

    messages_instance.send_msg("hello", secure=False)
    
    args, kwargs = mock_post.call_args
    data = json.loads(kwargs['data'])
    assert data['message_body']['message'] == "hello"
    assert data['message_body']['is_secure'] is False

@patch('requests.post')
def test_send_msg_secure(mock_post, messages_instance):
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_post.return_value = mock_response

    messages_instance.send_msg("secret", secure=True)
    
    args, kwargs = mock_post.call_args
    data = json.loads(kwargs['data'])
    assert data['message_body']['is_secure'] is True
    assert data['message_body']['message'] != "secret" # Should be encrypted
    assert data['message_body']['nonce'] != ""

@patch('requests.post')
def test_send_msg_failure(mock_post, messages_instance):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_post.return_value = mock_response

    messages_instance.send_msg("fail")
    
    messages_instance.logger.critical.assert_called()

def test_encrypt_decrypt_cycle(messages_instance):
    original_text = "This is a test message for encryption"
    encrypted, nonce = messages_instance.encrypt_message(original_text)
    
    # Simulate transport encoding (base64)
    enc_b64 = base64.urlsafe_b64encode(encrypted).decode('utf-8')
    nonce_b64 = base64.urlsafe_b64encode(nonce).decode('utf-8')
    
    decrypted = messages_instance.decrypt_message(enc_b64, nonce_b64)
    assert decrypted == original_text
