import logging

from .logging_protocol import LoggerRegistration
from jumpstarter.common import LogSource


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
