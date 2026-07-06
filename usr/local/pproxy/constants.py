CLAIMED_RECHECK_SECONDS = 30
CONFIG_FILE = '/etc/pproxy/config.ini'
CONFIG_SERVER_URL = "https://config.we-pn.com"
CONNECTIVITY_TEST_URLS = [
    "https://status.we-pn.com",
    "https://twitter.com",
    "https://google.com",
    "https://www.speedtest.net/",
    "https://www.cnn.com/",
    "https://bbc.co.uk",
    "https://connectivity.wepn.dev",
]
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
DEFAULT_GET_TIMEOUT = 10
DEFAULT_UPNP_TIMEOUT = 86400
ERROR_LOG_FILE = "/var/local/pproxy/error.log"
FORCE_SCREEN_ON = False
HEALTHY_DIAG_CODE = 127
HEARTBEATS_TO_WARM = 96
LOG_CONFIG = "/etc/pproxy/logging.ini"
MESSAGE_POLL_INTERVAL_SECONDS = 300
MESSAGE_POLL_JITTER_SECONDS = 60
METRICS_PORT = 8411
METRICS_REPORT_INTERVAL_SECONDS = 14460
MSG_CHANNEL_MAX_FRAME = 1048576
MSG_CHANNEL_PROTOCOL_VERSION = 1
MSG_CHANNEL_SOCKET = "/var/local/pproxy/msg_channel.sock"
SERVICE_FILE_BASE = "/var/local/pproxy/"
SKIP_OTA_CHECK = False
DEBUG_LOG_E2EE = False
