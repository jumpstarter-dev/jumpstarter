"""Callback system for output and user interaction.

This module provides a clean interface for handling output and user confirmations
without depending on CLI frameworks like click. This allows the library code to
be used in different contexts (CLI, web API, GUI, etc.).
"""

import logging
from typing import Protocol


class OutputCallback(Protocol):
    """Protocol for handling output and user interaction."""

    def progress(self, message: str) -> None:
        """Display a progress or informational message."""
        ...

    def success(self, message: str) -> None:
        """Display a success message."""
        ...

    def warning(self, message: str) -> None:
        """Display a warning message."""
        ...

    def error(self, message: str) -> None:
        """Display an error message."""
        ...

    def confirm(self, prompt: str) -> bool:
        """Ask user for confirmation. Returns True if confirmed, False otherwise."""
        ...


class SilentCallback:
    """Callback that produces no output and auto-confirms all prompts.

    Useful for scripting scenarios or when output should be suppressed
    (e.g., when using --output=name in CLI).
    """

    def progress(self, message: str) -> None:
        """Does nothing."""
        pass

    def success(self, message: str) -> None:
        """Does nothing."""
        pass

    def warning(self, message: str) -> None:
        """Does nothing."""
        pass

    def error(self, message: str) -> None:
        """Does nothing."""
        pass

    def confirm(self, prompt: str) -> bool:
        """Always returns True (auto-confirm)."""
        return True


class LoggingCallback:
    """Callback that uses Python's logging system.

    Useful for server applications or when you want structured logging.
    """

    def __init__(self, logger: logging.Logger = None):
        """Initialize with optional logger. If None, uses root logger."""
        self.logger = logger or logging.getLogger(__name__)

    def progress(self, message: str) -> None:
        """Log as INFO level."""
        self.logger.info(message)

    def success(self, message: str) -> None:
        """Log as INFO level."""
        self.logger.info(message)

    def warning(self, message: str) -> None:
        """Log as WARNING level."""
        self.logger.warning(message)

    def error(self, message: str) -> None:
        """Log as ERROR level."""
        self.logger.error(message)

    def confirm(self, prompt: str) -> bool:
        """Log the prompt and return True (auto-confirm for logging mode)."""
        self.logger.info(f"Confirmation requested: {prompt} (auto-confirmed)")
        return True


class ForceCallback:
    """Callback for force mode operations.

    Skips all confirmations and produces minimal output.
    """

    def __init__(self, output_callback: OutputCallback = None):
        """Initialize with optional output callback for messages."""
        self.output_callback = output_callback or SilentCallback()

    def progress(self, message: str) -> None:
        """Forward to output callback."""
        self.output_callback.progress(message)

    def success(self, message: str) -> None:
        """Forward to output callback."""
        self.output_callback.success(message)

    def warning(self, message: str) -> None:
        """Forward to output callback."""
        self.output_callback.warning(message)

    def error(self, message: str) -> None:
        """Forward to output callback."""
        self.output_callback.error(message)

    def confirm(self, prompt: str) -> bool:
        """Always returns True (force mode skips confirmations)."""
        return True
