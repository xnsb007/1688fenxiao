import logging
import os
import re
import sys
import threading
from datetime import datetime, timedelta


DEFAULT_RETENTION_DAYS = 30
LOG_DATE_FORMAT = "%Y%m%d"


def _parse_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalize_log_name(name):
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip())
    return normalized.strip(".-") or "app"


class DailyLogStream:
    """File-like stream that writes to name-YYYYMMDD.log and rolls by day."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, log_dir, name, retention_days=DEFAULT_RETENTION_DAYS, clock=None):
        self.log_dir = os.path.abspath(log_dir)
        self.name = _normalize_log_name(name)
        self.retention_days = _parse_positive_int(retention_days, DEFAULT_RETENTION_DAYS)
        self._clock = clock or datetime.now
        self._lock = threading.RLock()
        self._current_stamp = None
        self._file = None
        self._last_cleanup_stamp = None

        os.makedirs(self.log_dir, exist_ok=True)

    @property
    def current_path(self):
        with self._lock:
            self._ensure_open()
            return self._file.name

    def write(self, message):
        if not message:
            return 0

        if isinstance(message, (bytes, bytearray)):
            message = bytes(message).decode(self.encoding, self.errors)

        with self._lock:
            self._ensure_open()
            written = self._file.write(message)
            if "\n" in message:
                self._file.flush()
            return written

    def flush(self):
        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None
                self._current_stamp = None

    def cleanup_old_logs(self):
        with self._lock:
            self._last_cleanup_stamp = None
            self._cleanup_old_logs(self._clock())

    def isatty(self):
        return False

    def fileno(self):
        with self._lock:
            self._ensure_open()
            return self._file.fileno()

    def _ensure_open(self):
        now = self._clock()
        stamp = now.strftime(LOG_DATE_FORMAT)
        if self._file is not None and self._current_stamp == stamp:
            return

        if self._file is not None:
            self._file.flush()
            self._file.close()

        path = os.path.join(self.log_dir, f"{self.name}-{stamp}.log")
        self._file = open(path, "a", encoding=self.encoding, buffering=1)
        self._current_stamp = stamp
        self._cleanup_old_logs(now)

    def _cleanup_old_logs(self, now):
        today_stamp = now.strftime(LOG_DATE_FORMAT)
        if self._last_cleanup_stamp == today_stamp:
            return
        self._last_cleanup_stamp = today_stamp

        cutoff_date = now.date() - timedelta(days=self.retention_days - 1)
        filename_pattern = re.compile(rf"^{re.escape(self.name)}-(\d{{8}})\.log$")

        try:
            filenames = os.listdir(self.log_dir)
        except OSError:
            return

        for filename in filenames:
            match = filename_pattern.match(filename)
            if not match:
                continue

            try:
                file_date = datetime.strptime(match.group(1), LOG_DATE_FORMAT).date()
            except ValueError:
                continue

            if file_date >= cutoff_date:
                continue

            try:
                os.remove(os.path.join(self.log_dir, filename))
            except OSError:
                pass


class _DailyLogHandler(logging.StreamHandler):
    daily_file_logging = True

    def __init__(self, stream):
        super().__init__(stream)
        self.daily_log_name = stream.name


def setup_daily_file_logging(name="web", log_dir=None, retention_days=None, level=None, redirect_stdio=True):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = log_dir or os.path.join(base_dir, "logs")
    retention_days = _parse_positive_int(
        retention_days or os.environ.get("LOG_RETENTION_DAYS"),
        DEFAULT_RETENTION_DAYS,
    )
    log_level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    normalized_name = _normalize_log_name(name)

    existing_stream = sys.stdout
    if not isinstance(existing_stream, DailyLogStream) or existing_stream.name != normalized_name:
        existing_stream = DailyLogStream(log_dir, normalized_name, retention_days)
    else:
        existing_stream.retention_days = retention_days
        existing_stream.cleanup_old_logs()

    if redirect_stdio:
        sys.stdout = existing_stream
        sys.stderr = existing_stream

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if not getattr(handler, "daily_file_logging", False):
            continue

        root_logger.removeHandler(handler)
        handler_stream = getattr(handler, "stream", None)
        if isinstance(handler_stream, DailyLogStream) and handler_stream is not existing_stream:
            handler_stream.close()
        handler.close()

    handler = _DailyLogHandler(existing_stream)
    handler.setLevel(log_level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))

    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
    logging.captureWarnings(True)

    return existing_stream.current_path
