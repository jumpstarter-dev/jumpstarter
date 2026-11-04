"""Map Jumpstarter errors to Robot Framework failures."""

from __future__ import annotations

import contextlib
from collections.abc import Generator

from jumpstarter.client.core import DriverError
from jumpstarter.common.exceptions import JumpstarterException


class JumpstarterRobotError(JumpstarterException):
    """A problem with how the Robot Framework library is set up or used."""


@contextlib.contextmanager
def mapped_errors(keyword: str) -> Generator[None]:
    """Turn driver and connection errors raised by ``keyword`` into test failures.

    Robot Framework already fails a test on any exception; this keeps the
    keyword's name in the message and drops the gRPC noise. Programming errors
    (TypeError, AttributeError from the library itself) propagate unchanged.
    """
    from robot.api import Failure, FatalError

    from . import sessions

    try:
        yield
    except sessions.LeaseEnded as e:
        if sessions.lease_end_policy(e.session) == "fatal":
            raise FatalError(f"{keyword}: {e}") from e
        raise Failure(f"{keyword}: {e}") from e
    except DriverError as e:
        raise Failure(f"{keyword}: {e}") from e
    except ImportError as e:
        # StubDriverClient: the driver's client package is missing or not allowed
        raise Failure(f"{keyword}: {e}") from e
    except JumpstarterException as e:
        raise Failure(f"{keyword}: {e}") from e
