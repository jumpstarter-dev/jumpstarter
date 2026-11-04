"""Helpers for testing Robot Framework suites without a controller.

Run suites in-process with ``robot.run()`` against a client served by
``jumpstarter.common.utils.serve()``::

    with serve(MockPower()) as client, use_client(client):
        robot.run("tests/power.robot")
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

from . import context, sessions
from jumpstarter.client import DriverClient


def reset() -> None:
    """Return the module-level state to a fresh run: no sessions, factories or scoped resources.

    Sessions and scoped resources are shared by every library instance of a
    run, so tests that run several suites in one process call this between them.
    """
    context.end_run()
    sessions.reset()


@contextmanager
def use_client(
    client: DriverClient,
    session: str = sessions.DEFAULT_SESSION,
    *,
    exporter: str = "test",
    labels: dict[str, str] | None = None,
) -> Generator[sessions.Session]:
    """Make ``client`` the connected session ``session`` while the block runs."""
    opened = sessions.install(sessions.Session.from_client(client, session, exporter=exporter, labels=labels))
    try:
        yield opened
    finally:
        sessions.close(session)


@contextmanager
def use_session_factory(
    *,
    lease: Callable[..., sessions.Session] | None = None,
    local: Callable[..., sessions.Session] | None = None,
    shell: Callable[..., sessions.Session] | None = None,
) -> Generator[None]:
    """Replace how leases, local exporters or the shell are opened while the block runs."""
    saved = sessions.open_lease, sessions.open_local, sessions.open_shell
    if lease is not None:
        sessions.open_lease = lease
    if local is not None:
        sessions.open_local = local
    if shell is not None:
        sessions.open_shell = shell
    try:
        yield
    finally:
        sessions.open_lease, sessions.open_local, sessions.open_shell = saved
        sessions.close_all()
