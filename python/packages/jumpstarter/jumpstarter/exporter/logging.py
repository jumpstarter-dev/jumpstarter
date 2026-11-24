import logging
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock, RLock

from google.protobuf.timestamp_pb2 import Timestamp
from jumpstarter_protocol import jumpstarter_pb2

from .logging_protocol import LoggerRegistration
from jumpstarter.common import LogSource

_FIRST_CLASS_KEYS = frozenset(("driver_type", "operation"))
_STDLIB_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())


class LogFanOut:
    """Hands every log record to each open LogStream.

    Each client sharing a lease opens its own LogStream, so readers taking turns
    at one queue would each see only part of the logs. Records logged while no
    stream is open wait in a backlog for the next stream, so a client that
    connects after a hook started still sees its output.
    """

    def __init__(self, maxlen: int = 256):
        self._maxlen = maxlen
        # append() runs on whichever thread logged; subscribe() on the event loop
        self._lock = Lock()
        self._backlog: deque = deque(maxlen=maxlen)
        self._subscribers: list[deque] = []

    def append(self, record) -> None:
        with self._lock:
            if not self._subscribers:
                self._backlog.append(record)
            for queue in self._subscribers:
                queue.append(record)

    @contextmanager
    def subscribe(self) -> Generator[deque]:
        """Yield a queue that receives the backlog, then every new record."""
        with self._lock:
            queue = deque(self._backlog, maxlen=self._maxlen)
            self._backlog.clear()
            self._subscribers.append(queue)
        try:
            yield queue
        finally:
            with self._lock:
                # By identity: deques holding the same records compare equal
                self._subscribers = [q for q in self._subscribers if q is not queue]
                if not self._subscribers:
                    # The backlog is empty while any stream is open, so this keeps order
                    self._backlog.extend(queue)


class LogHandler(logging.Handler):
    def __init__(self, queue: deque | LogFanOut, source: LogSource = LogSource.UNSPECIFIED):
        logging.Handler.__init__(self)
        self.queue = queue
        self.listener = None
        self.source = source  # LogSource enum value
        self._lock = RLock()
        self._child_handlers = {}  # Dict of logger_name -> LogSource mappings

    def add_child_handler(self, logger_name: str, source: LogSource):
        """Add a child handler that will route logs from a specific logger with a different source."""
        with self._lock:
            self._child_handlers[logger_name] = source

    def remove_child_handler(self, logger_name: str):
        """Remove a child handler mapping."""
        with self._lock:
            self._child_handlers.pop(logger_name, None)

    def get_source_for_record(self, record):
        """Determine the appropriate log source for a record."""
        with self._lock:
            # Check if this record comes from a logger with a specific source mapping
            logger_name = record.name
            for mapped_logger, source in self._child_handlers.items():
                if logger_name.startswith(mapped_logger):
                    return source
            return self.source

    def enqueue(self, record):
        self.queue.append(record)

    def prepare(self, record):
        source = self.get_source_for_record(record)
        kwargs = {
            "uuid": "",
            "severity": record.levelname,
            "message": record.getMessage(),
            "source": source.value,
        }
        if hasattr(record, "driver_type"):
            kwargs["driver_type"] = record.driver_type
        if hasattr(record, "operation"):
            kwargs["operation"] = record.operation
        ts = Timestamp()
        ts.FromDatetime(datetime.fromtimestamp(record.created, tz=UTC))
        kwargs["timestamp"] = ts
        structured = {}
        for key, val in record.__dict__.items():
            if key in _STDLIB_RECORD_ATTRS or key in _FIRST_CLASS_KEYS:
                continue
            if val is not None:
                structured[key] = str(val)
        if structured:
            kwargs["structured_fields"] = structured
        return jumpstarter_pb2.LogStreamResponse(**kwargs)  # ty: ignore[invalid-argument-type]

    def emit(self, record):
        try:
            self.enqueue(self.prepare(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)

    @contextmanager
    def context_log_source(self, logger_name: str, source: LogSource):
        """Context manager to temporarily set a log source for a specific logger."""
        self.add_child_handler(logger_name, source)
        try:
            yield
        finally:
            self.remove_child_handler(logger_name)


def get_logger(
    name: str, source: LogSource = LogSource.SYSTEM, session: LoggerRegistration | None = None
) -> logging.Logger:
    """
    Get a logger with automatic LogSource mapping.

    Args:
        name: Logger name (e.g., __name__ or custom name)
        source: The LogSource to associate with this logger
        session: Optional session to register with immediately

    Returns:
        A standard Python logger instance
    """
    logger = logging.getLogger(name)

    # If session provided, register the source mapping
    if session:
        session.add_logger_source(name, source)

    return logger
