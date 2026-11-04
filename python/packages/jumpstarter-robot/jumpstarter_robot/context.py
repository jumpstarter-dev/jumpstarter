"""Services shared by the keyword sets: scoped resources, output files and results.

Consoles, port forwards, Appium servers and driver contexts stay open across
keywords. Each is registered with a scope (``test``, ``suite`` or ``global``)
and with its session, and is closed at the end of that scope or before the
session's connection goes away, whichever comes first.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractAsyncContextManager, ExitStack
from pathlib import Path
from typing import Any, Literal

from anyio.from_thread import BlockingPortal

from . import sessions
from .convert import to_robot as _to_robot

logger = logging.getLogger(__name__)

Scope = Literal["test", "suite", "global"]
SCOPES = ("test", "suite", "global")

_lock = threading.RLock()
_scoped: list[tuple[str, str | None, Callable[[], None]]] = []  # (scope, suite id, closer)
_counter = itertools.count(1)


def _once(closer: Callable[[], None]) -> Callable[[], None]:
    done = threading.Event()

    def run() -> None:
        if done.is_set():
            return
        done.set()
        closer()

    return run


def _current_suite_id() -> str | None:
    from robot.running.context import EXECUTION_CONTEXTS

    context = EXECUTION_CONTEXTS.current
    return context.suite.id if context is not None else None


def _in_test() -> bool:
    from robot.running.context import EXECUTION_CONTEXTS

    context = EXECUTION_CONTEXTS.current
    return context is not None and context.test is not None


def add_scoped(
    closer: Callable[[], None], scope: str = "suite", session: sessions.Session | None = None
) -> Callable[[], None]:
    """Close ``closer`` at the end of ``scope`` or with ``session``; returns the idempotent closer.

    A ``test`` scope requested outside a test (in a suite setup) becomes the
    suite scope, which is where such a resource naturally ends.
    """
    scope = scope.lower()
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, not '{scope}'")
    if scope == "test" and not _in_test():
        scope = "suite"
    run = _once(closer)
    with _lock:
        _scoped.append((scope, _current_suite_id() if scope == "suite" else None, run))
    if session is not None:
        session.on_close(run)
    return run


def _close_where(predicate: Callable[[str, str | None], bool]) -> None:
    with _lock:
        matching = [entry for entry in _scoped if predicate(entry[0], entry[1])]
        for entry in matching:
            _scoped.remove(entry)
    for _, _, closer in reversed(matching):
        try:
            closer()
        except Exception:
            logger.warning("error while closing a scoped resource", exc_info=True)


def end_test() -> None:
    _close_where(lambda scope, _: scope == "test")


def end_suite(suite_id: str) -> None:
    _close_where(lambda scope, owner: scope == "suite" and (owner == suite_id or owner is None))
    sessions.end_suite(suite_id)


def end_run() -> None:
    _close_where(lambda scope, _: True)


def output_dir() -> Path:
    """``${OUTPUT DIR}/jumpstarter``, or ``./jumpstarter`` outside a run."""
    from robot.libraries.BuiltIn import BuiltIn, RobotNotRunningError

    try:
        base = Path(BuiltIn().get_variable_value("${OUTPUT DIR}") or ".")
    except RobotNotRunningError:
        base = Path(".")
    path = base / "jumpstarter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(stem: str, suffix: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in stem).strip("-") or "artifact"
    return output_dir() / f"{safe}-{next(_counter)}{suffix}"


def embed_image(path: Path, width: int = 480) -> None:
    """Show an image in the Robot log, linked relative to the log file."""
    from robot.api import logger as robot_logger

    try:
        relative = path.relative_to(output_dir().parent)
    except ValueError:
        relative = path
    robot_logger.info(f'<a href="{relative}"><img src="{relative}" width="{width}"></a>', html=True)


def save_image(image: Any, stem: str = "image") -> Path:
    path = artifact_path(stem, ".png")
    image.save(path)
    embed_image(path)
    return path


def to_robot(value: Any, *, max_items: int = 1000, stem: str = "image") -> Any:
    return _to_robot(value, max_items=max_items, save_image=lambda image: save_image(image, stem))


SESSION_MARKER = "_jumpstarter_session_name"


def _suite_session_names() -> list[str]:
    """Sessions declared by the libraries imported into the running suite."""
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    if current is None:
        return []
    try:
        instances = current.namespace.get_library_instances().values()
    except Exception:  # noqa: BLE001 - not every context has a namespace
        return []
    names = {getattr(instance, SESSION_MARKER) for instance in instances if getattr(instance, SESSION_MARKER, None)}
    return sorted(names)


def session_name(explicit: str | None = None) -> str:
    """The session a keyword uses: the one it names, the one `Switch Session` chose,
    the only one declared in the suite, or ``default``."""
    if explicit:
        return explicit
    if sessions.default_switched():
        return sessions.default_name()
    declared = _suite_session_names()
    if len(declared) == 1:
        return declared[0]
    return sessions.default_name()


def switch_session(name: str) -> str:
    """`Switch Session`: make ``name`` the default until the running suite ends; returns the previous one."""
    return sessions.set_default(name, suite=_current_suite_id())


def session(explicit: str | None = None) -> sessions.Session:
    """Open (if needed) and return the session a keyword uses."""
    return sessions.get(session_name(explicit))


def close_session(name: str) -> None:
    sessions.close(name)


def enter_cancellable(portal: BlockingPortal, manager: AbstractAsyncContextManager, stack: ExitStack) -> Any:
    """Enter ``manager`` in the session's portal, cancelling the enter if the keyword is interrupted.

    An RF timeout or Ctrl-C interrupts only the wait in this thread. The enter
    keeps running in the portal, and once entered it waits for an exit that
    never comes, which blocks the portal, and so the session, from closing.
    """
    blocking = portal.wrap_async_context_manager(manager)
    try:
        value = blocking.__enter__()
    except BaseException:
        # anyio keeps the task that runs the context manager here; cancelling it
        # cancels a pending enter, or exits a context that was entered meanwhile
        task = getattr(blocking, "_exit_future", None)
        if isinstance(task, Future):
            task.cancel()
        raise
    stack.push(blocking.__exit__)
    return value
