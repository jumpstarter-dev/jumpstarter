"""Services shared by the keyword sets: scoped resources, output files and results.

Port forwards and driver contexts stay open across
keywords. Each is registered with a scope (``test``, ``suite`` or ``global``)
and with its session, and is closed at the end of that scope or before the
session's connection goes away, whichever comes first.
"""

from __future__ import annotations

import _thread
import contextvars
import itertools
import logging
import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import Future
from contextlib import AbstractAsyncContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Literal

import anyio
from anyio.from_thread import BlockingPortal

from . import sessions
from .convert import to_robot as _to_robot
from jumpstarter.client import DriverClient

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


def check_scope(scope: str) -> str:
    """``scope`` in its normal form, or a ValueError for one that does not exist.

    Keywords call this before they open anything: `add_scoped` runs last, and
    by then a mistyped scope would leave the connection open with no closer.
    """
    normal = str(scope).lower()
    if normal not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, not '{scope}'")
    return normal


def add_scoped(
    closer: Callable[[], None], scope: str = "suite", session: sessions.Session | None = None
) -> Callable[[], None]:
    """Close ``closer`` at the end of ``scope`` or with ``session``; returns the idempotent closer.

    A ``test`` scope requested outside a test (in a suite setup) becomes the
    suite scope, which is where such a resource naturally ends.
    """
    scope = check_scope(scope)
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
    sessions.end_run()


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


def suite_session_names() -> list[str]:
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
    declared = suite_session_names()
    if len(declared) == 1:
        return declared[0]
    return sessions.default_name()


def switch_session(name: str) -> str:
    """`Switch Session`: make ``name`` the default until the running suite ends; returns the previous one."""
    # not the registry's default: with one session declared in the suite and no
    # earlier switch, that session is the one in effect
    previous = session_name()
    sessions.set_default(name, suite=_current_suite_id())
    return previous


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


# How long an interrupted keyword waits for its call in the portal to stop.
CANCEL_GRACE = 5.0


def _in_thread(call: Callable[[], Any]) -> SimpleQueue[tuple[Any, BaseException | None]]:
    """Start ``call`` in a thread; its result or exception arrives in the queue.

    An RF timeout or Ctrl-C raises in the main thread wherever it is. Inside
    the locks of a ``Future`` or ``Thread`` that can leave a lock held for
    good, which blocks the portal's event loop. ``SimpleQueue`` has no such
    lock, so this is what the main thread waits on.
    """
    outcome: SimpleQueue[tuple[Any, BaseException | None]] = SimpleQueue()
    run_in_context = contextvars.copy_context().run

    def run() -> None:
        try:
            result = run_in_context(call)
        except BaseException as e:  # noqa: BLE001 - raised or logged by whoever reads the queue
            outcome.put((None, e))
        else:
            outcome.put((result, None))

    _thread.start_new_thread(run, ())
    return outcome


def _wait(outcome: SimpleQueue[tuple[Any, BaseException | None]], deadline: float) -> None:
    """Wait for a cleanup until ``deadline``; it never replaces the error being handled."""
    try:
        _, error = outcome.get(timeout=max(0.0, deadline - time.monotonic()))
    except Empty:
        return
    if error is not None:
        logger.debug("cannot stop a driver call", exc_info=error)


class _PortalCall:
    """One async call in a portal that another thread can cancel."""

    def __init__(self, portal: BlockingPortal, func: Callable[..., Awaitable[Any]], args: tuple):
        self.portal, self.func, self.args = portal, func, args
        self.scopes: list[anyio.CancelScope] = []
        self.interrupted = threading.Event()
        self.done = threading.Event()

    async def run(self) -> Any:
        with anyio.CancelScope() as scope:
            self.scopes.append(scope)
            try:
                return None if self.interrupted.is_set() else await self.func(*self.args)
            finally:
                self.done.set()

    def stop(self) -> None:
        self.portal.call(self._cancel)
        if self.scopes:
            self.done.wait(CANCEL_GRACE)
        else:
            self.done.set()  # run() has not started, and does nothing once it does

    def _cancel(self) -> None:
        for scope in self.scopes:
            scope.cancel()


def call_cancellable(
    portal: BlockingPortal,
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    stuck: threading.Event | None = None,
) -> Any:
    """``portal.call(func, *args)`` that cancels ``func`` when the keyword is interrupted.

    ``portal.call`` returns on an RF timeout or Ctrl-C but leaves ``func``
    running in the portal: a stream being read stays open on the exporter.
    ``stuck`` is set when it did not stop within ``CANCEL_GRACE``.
    """
    call = _PortalCall(portal, func, args)
    try:
        result, error = _in_thread(lambda: portal.call(call.run)).get()
    except BaseException:
        call.interrupted.set()
        if not call.done.is_set():
            _wait(_in_thread(call.stop), time.monotonic() + CANCEL_GRACE)
            if stuck is not None and not call.done.is_set():
                stuck.set()
        raise
    if error is not None:
        raise error
    return result


async def _cancel_stream(stream: Any) -> None:
    """End a driver stream on the exporter by cancelling a read of it.

    gRPC cancels the call when a pending read is cancelled; closing
    ``streamingcall_async`` leaves it running until the connection closes.
    """
    with anyio.CancelScope() as scope:
        scope.cancel()
        async for _ in stream:  # items already received; the cancellation lands on the next read
            pass
    with anyio.CancelScope(shield=True):
        await stream.aclose()


@dataclass(eq=False)
class _Stream:
    """A driver stream a keyword reads through a generator."""

    node: DriverClient
    stream: Any
    generator: weakref.ref | None = None
    reading: bool = False  # from the first read until the stream ends by itself
    stuck: threading.Event = field(default_factory=threading.Event)

    def items(self) -> Generator[Any]:
        # No ``finally``: it would run in the finalizer of a dropped generator,
        # where Python discards an RF timeout. See _end_dropped.
        while True:
            self.reading = True
            try:
                item = call_cancellable(self.node.portal, self.stream.__anext__, stuck=self.stuck)
            except StopAsyncIteration:
                self.reading = False
                return
            yield item

    def dropped(self) -> bool:
        generator = None if self.generator is None else self.generator()
        return generator is None or generator.gi_frame is None


# A generator that is still alive keeps its stream, also after the keyword.
_streams: list[_Stream] = []


def _end_dropped() -> list[SimpleQueue]:
    """Start ending the streams whose generators were closed or dropped."""
    outcomes = []
    for entry in list(_streams):
        if not entry.dropped():
            continue
        # a stuck read was cancelled already
        if entry.reading and not entry.stuck.is_set():
            outcomes.append(_in_thread(partial(entry.node.portal.call, _cancel_stream, entry.stream)))
        _streams.remove(entry)
    return outcomes


def _registered_streamingcall(node: DriverClient, ending: list[SimpleQueue]):
    def streamingcall(method, *args):
        ending[:] = [outcome for outcome in ending if outcome.empty()]
        ending.extend(_end_dropped())  # a client may poll with many short streams
        entry = _Stream(node, node.streamingcall_async(method, *args))
        generator = entry.items()
        entry.generator = weakref.ref(generator)
        _streams.append(entry)
        return generator

    return streamingcall


@contextmanager
def bounded_streams(node: DriverClient | None) -> Generator[None]:
    """Within this block, a driver stream of ``node`` ends on the exporter once its generator is closed or dropped.

    Keywords read at most ``max_items`` of a generator. The client's own
    ``streamingcall`` would leave the rest of the stream running.
    """
    patched = []
    ending = _end_dropped()
    pending = [] if node is None else [node]
    while pending:
        client = pending.pop()
        pending.extend(getattr(client, "children", {}).values())
        own = getattr(type(client), "streamingcall", None) is not DriverClient.streamingcall
        if not own and "streamingcall" not in vars(client):
            client.streamingcall = _registered_streamingcall(client, ending)
            patched.append(client)
    try:
        yield
    finally:
        for client in patched:
            del client.streamingcall
        deadline = time.monotonic() + CANCEL_GRACE
        for outcome in [*ending, *_end_dropped()]:
            _wait(outcome, deadline)
