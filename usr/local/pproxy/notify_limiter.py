import hashlib
import time

from constants import (
    NOTIFY_DAILY_LIMIT,
    NOTIFY_ENTRY_TTL_SECONDS,
    NOTIFY_MAX_ENTRIES,
)

NOTIFY_SECTION = "notify_limits"
DAILY_WINDOW_KEY = "daily_window_start"
DAILY_COUNT_KEY = "daily_count"


class NotificationLimiter:
    def __init__(self, logger, status):
        self.logger = logger
        self.status = status
        if not self.status.has_section(NOTIFY_SECTION):
            self.status.add_section(NOTIFY_SECTION)

    def allow(self, kind, recipient, cooldown, token=None):
        now = int(time.time())
        key = self._key(kind, recipient)

        if self.status.has_option(NOTIFY_SECTION, key):
            last_sent, stored_token = self._parse_entry(
                self.status.get_field(NOTIFY_SECTION, key))
            if last_sent is not None:
                if (token is not None and stored_token == token
                        and now - last_sent < NOTIFY_ENTRY_TTL_SECONDS):
                    self.logger.info(
                        "notify: suppressing duplicate " + kind + " for " + key)
                    return False
                if 0 <= now - last_sent < cooldown:
                    self.logger.info(
                        "notify: suppressing " + kind + " for " + key + " (cooldown)")
                    return False
                if now < last_sent:
                    self.logger.warning(
                        "notify: clock moved backwards for " + key + ", allowing")

        start, count = self._daily_window(now)
        if count >= NOTIFY_DAILY_LIMIT:
            self.logger.info(
                "notify: suppressing " + kind + " for " + key + " (daily cap)")
            return False

        self.status.set_field(NOTIFY_SECTION, key,
                              str(now) + "|" + (token if token is not None else ""))
        self.status.set_field(NOTIFY_SECTION, DAILY_WINDOW_KEY, start)
        self.status.set_field(NOTIFY_SECTION, DAILY_COUNT_KEY, count + 1)
        self._prune(now)
        self.status.save()
        return True

    def _key(self, kind, recipient):
        digest = hashlib.sha256(recipient.encode("utf-8")).hexdigest()[:12]
        return kind + "_" + digest

    def _parse_entry(self, raw):
        try:
            last_sent_str, stored = raw.split("|", 1)
            last_sent = int(last_sent_str)
        except (ValueError, AttributeError):
            return None, None
        return last_sent, (stored or None)

    def _daily_window(self, now):
        if not self.status.has_option(NOTIFY_SECTION, DAILY_WINDOW_KEY):
            return now, 0
        try:
            start = int(self.status.get_field(NOTIFY_SECTION, DAILY_WINDOW_KEY))
            count = int(self.status.get_field(NOTIFY_SECTION, DAILY_COUNT_KEY))
        except (TypeError, ValueError):
            return now, 0
        if now - start >= 86400 or now < start:
            return now, 0
        return start, count

    def _prune(self, now):
        cfg = self.status.status
        if not cfg.has_section(NOTIFY_SECTION):
            return
        entries = []
        for opt in cfg.options(NOTIFY_SECTION):
            if opt in (DAILY_WINDOW_KEY, DAILY_COUNT_KEY):
                continue
            last_sent, _ = self._parse_entry(
                self.status.get_field(NOTIFY_SECTION, opt))
            if last_sent is None:
                continue
            if now - last_sent >= NOTIFY_ENTRY_TTL_SECONDS:
                cfg.remove_option(NOTIFY_SECTION, opt)
            else:
                entries.append((last_sent, opt))
        if len(entries) > NOTIFY_MAX_ENTRIES:
            entries.sort(key=lambda e: e[0])
            for _, opt in entries[:len(entries) - NOTIFY_MAX_ENTRIES]:
                cfg.remove_option(NOTIFY_SECTION, opt)
