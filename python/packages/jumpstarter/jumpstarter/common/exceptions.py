import sys


class JumpstarterException(Exception):
    """Base class for jumpstarter-specific errors.

    This class should not be raised directly, but should be used as a base
    class for all jumpstarter-specific errors.
    It handles the __cause__ attribute so the jumpstarter errors could be raised as

    .. code-block:: python

        raise SomeError("message") from original_exception
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self._config = None

    def __str__(self):
        if self.__cause__:
            return f"{self.message} (Caused by: {self.__cause__})"
        return f"{self.message}"


    # some exceptions need to able to set the config that caused the error
    # to attempt recovery, or re-authentication if the token is expired
    def set_config(self, config):
        self._config = config

    def get_config(self):
        return self._config


    def print(self, message: str | None = None):
        ANSI_RED = "\033[91m"
        ANSI_CLEAR = "\033[0m"
        print(f"{ANSI_RED}{self}{ANSI_CLEAR}", file=sys.stderr)


class ConnectionError(JumpstarterException):
    """Raised when a connection to a jumpstarter server fails."""

    pass


class ConfigurationError(JumpstarterException):
    """Raised when a configuration error exists."""

    pass


class ArgumentError(JumpstarterException):
    """Raised when a cli argument is not valid."""

    pass


class FileAccessError(JumpstarterException):
    """Raised when a file access error occurs."""

    pass


class FileNotFoundError(JumpstarterException, FileNotFoundError):
    """Raised when a file is not found."""

    pass


class ReauthenticationFailed(JumpstarterException):
    """Raised when a re-authentication fails."""

    pass
