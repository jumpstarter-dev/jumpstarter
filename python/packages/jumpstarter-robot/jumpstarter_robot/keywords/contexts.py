"""Generic keywords for driver context managers, streams and properties.

Some client methods are context managers: ``AdbClient.forward_adb`` keeps a
port forward open, ``UbootConsole.reboot_to_console`` holds a console, and a
QEMU ``shell`` holds a connection. A Robot keyword cannot span a ``with``
block, so `Open Driver Context` enters one, keeps it open under an alias, and
closes it at the end of its scope (test, suite or run) or before its session
disconnects, whichever comes first.

Streaming driver calls (power readings, logs) can be endless. `Collect Driver
Stream` reads one for a number of items or a length of time, and then cancels
it on the exporter as well.
"""

from __future__ import annotations

import inspect
import itertools
import threading
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager, ExitStack
from dataclasses import dataclass
from typing import Any

import anyio
from anyio.from_thread import BlockingPortal
from robot.api import Failure, logger
from robot.api.deco import keyword
from robot.utils import timestr_to_secs

from .. import context, sessions
from ..errors import mapped_errors
from ..signatures import convert_call
from jumpstarter.client import DriverClient


@dataclass(eq=False)
class OpenContext:
    """A context manager entered by `Open Driver Context` and still open."""

    alias: str
    path: str
    method: str
    session: str
    value: Any
    stack: ExitStack
    close: Callable[[], None] = lambda: None


# Open contexts by alias, oldest first. Module level, like the session
# registry, so every Jumpstarter library instance of the run sees them.
_lock = threading.RLock()
_open: dict[str, OpenContext] = {}


def open_contexts() -> list[OpenContext]:
    """The contexts still open, oldest first."""
    with _lock:
        return list(_open.values())


def _unique_alias(base: str) -> str:
    if base not in _open:
        return base
    for n in itertools.count(2):
        candidate = f"{base}_{n}"
        if candidate not in _open:
            return candidate
    raise AssertionError("unreachable")


def _check_alias_free(alias: str | None) -> None:
    with _lock:
        if alias is not None and alias in _open:
            raise Failure(f"a driver context '{alias}' is already open; close it first or use another alias")


def _lookup(alias: str | None) -> OpenContext:
    with _lock:
        if not _open:
            raise Failure("no driver context is open")
        if alias is None:
            return next(reversed(_open.values()))
        if alias not in _open:
            raise Failure(f"no driver context '{alias}' is open; open contexts: {sorted(_open)}")
        return _open[alias]


def _robot_value(value: Any, stem: str) -> Any:
    """``to_robot``, with tuples such as ``(host, port)`` turned into lists."""
    if isinstance(value, tuple):
        value = list(value)
    return context.to_robot(value, stem=stem)


def resolve(path: str, session: str | None) -> tuple[sessions.Session, DriverClient]:
    """The session and the driver at ``path`` a keyword talks to, failing cleanly if absent."""
    target = context.session(session)
    target.check_alive()
    try:
        return target, target.resolve(path)
    except KeyError as e:
        raise Failure(e.args[0]) from None


def _enter(node: DriverClient, method: str, args: tuple, kwargs: dict, stack: ExitStack) -> Any:
    factory = getattr(node, method, None)
    if not callable(factory):
        raise Failure(f"driver client {type(node).__name__} has no method '{method}'")
    call_args, call_kwargs = convert_call(factory, args, kwargs)
    manager = factory(*call_args, **call_kwargs)
    if isinstance(manager, AbstractContextManager):
        return stack.enter_context(manager)
    if isinstance(manager, AbstractAsyncContextManager):
        return context.enter_cancellable(node.portal, manager, stack)
    raise Failure(f"{type(node).__name__}.{method} did not return a context manager; use Call Driver instead")


# How long an interrupted keyword waits for its cancelled task to finish in the portal
CANCEL_GRACE = 5.0


def _call_cancellable(portal: BlockingPortal, func: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    """``portal.call(func, *args)`` that cancels ``func`` when the keyword is interrupted.

    ``portal.call`` returns on an RF timeout or Ctrl-C but leaves ``func``
    running in the portal: a stream being read stays open on the exporter.
    """
    finished = threading.Event()

    async def run() -> Any:
        try:
            return await func(*args)
        finally:
            finished.set()

    future = portal.start_task_soon(run)
    try:
        return future.result()
    except BaseException:
        future.cancel()
        finished.wait(CANCEL_GRACE)
        raise


def _register(
    alias: str | None, path: str, method: str, target: Any, value: Any, stack: ExitStack, scope: str
) -> OpenContext:
    """Record an entered context under its alias and close it with its scope or session."""
    with _lock:
        try:
            _check_alias_free(alias)
        except Failure:
            stack.close()
            raise
        name = alias if alias is not None else _unique_alias(method)
        entry = OpenContext(name, path, method, target.name, None, stack)
        _open[name] = entry
    try:
        entry.value = _robot_value(value, stem=name)
        entry.close = context.add_scoped(lambda: _finish(entry), scope, target)
    except BaseException:
        _finish(entry)
        raise
    return entry


def _finish(entry: OpenContext) -> None:
    """Exit ``entry``'s context and forget it; run once through its scoped closer."""
    with sessions.lock():
        with _lock:
            if _open.get(entry.alias) is entry:
                del _open[entry.alias]
        entry.stack.close()


async def _collect(node: DriverClient, method: str, args: tuple, count: int | None, seconds: float | None) -> list:
    """Read ``method``'s stream until ``count`` items or ``seconds`` pass, then cancel it.

    The stream is stopped by cancelling a pending read, never by breaking out
    of the loop: gRPC cancels the call (and the exporter its stream) when a
    read is cancelled, while closing ``streamingcall_async`` early leaves the
    call running on the exporter until the connection closes.
    """
    items: list[Any] = []
    stream = node.streamingcall_async(method, *args)
    try:
        with anyio.move_on_after(seconds) as scope:
            async for item in stream:
                if scope.cancel_called:
                    continue  # already buffered; the cancellation lands on the next read
                items.append(item)
                if count is not None and len(items) >= count:
                    scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            await stream.aclose()
    return items


def _stream_limits(count: int | None, timeout: str | float | None) -> tuple[int | None, float | None]:
    if isinstance(timeout, str) and timeout.strip().upper() in ("", "NONE"):
        timeout = None  # Robot passes None as text to a str argument
    if count is None and timeout is None:
        raise Failure("Collect Driver Stream needs count, timeout or both; an endless stream would never return")
    if count is not None and count < 0:
        raise Failure(f"count must not be negative, not {count}")
    seconds = timestr_to_secs(timeout) if timeout is not None else None
    if seconds is not None and seconds < 0:
        raise Failure(f"timeout must not be negative, not {timeout}")
    return count, seconds


class ContextKeywords:
    """Driver context managers, streaming calls and properties by driver path."""

    @keyword
    def open_driver_context(
        self,
        path: str,
        method: str,
        *args: Any,
        alias: str | None = None,
        scope: str = "suite",
        session: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Enter a client method that is a context manager and keep it open.

        Calls ``method`` of the driver at ``path`` with ``args`` and
        ``kwargs``, enters the context manager it returns, and returns what
        it yields: a tuple such as ``(host, port)`` becomes a list, a
        pydantic model or dataclass a dictionary, anything else is returned
        as is. Asynchronous context managers work too.

        The context stays open until `Close Driver Context`, the end of
        ``scope`` (``test``, ``suite`` or ``global`` for the whole run), or
        until its session disconnects, whichever comes first. A ``test``
        scope used in a suite setup lasts for the suite.

        ``alias`` names the context for `Close Driver Context` and `Get
        Driver Context Value`. It defaults to the method name, with ``_2``,
        ``_3`` added when that one is already open; an explicit alias that
        is already open fails. ``alias``, ``scope`` and ``session`` are taken
        by this keyword and are not passed to the method. Text arguments are
        converted to the method's annotated types, as for driver keywords.

        | ${forward}= | Open Driver Context | android.adb | forward_adb | port=${0} | alias=adb |
        | Log | ADB server at ${forward}[0]:${forward}[1] |
        | Open Driver Context | dut.console | reboot_to_console | scope=test |
        | Close Driver Context | adb |
        """
        if scope.lower() not in context.SCOPES:
            raise Failure(f"scope must be one of {', '.join(context.SCOPES)}, not '{scope}'")
        with sessions.lock(), mapped_errors("Open Driver Context"):
            _check_alias_free(alias)
            target, node = resolve(path, session)
            stack = ExitStack()
            try:
                value = _enter(node, method, args, kwargs, stack)
            except BaseException:
                stack.close()
                raise
            entry = _register(alias, path, method, target, value, stack, scope)
            logger.info(f"opened driver context '{entry.alias}' ({path}.{method}, {scope} scope)")
            return entry.value

    @keyword
    def open_port_forward(
        self,
        path: str,
        local_port: int = 0,
        method: str = "connect",
        alias: str | None = None,
        scope: str = "suite",
        session: str | None = None,
    ) -> str:
        """Forward a local TCP port to a driver's byte stream; returns ``host:port``.

        Anything that speaks TCP can then reach the device through the
        Jumpstarter tunnel: an HTTP API, a database, or a Robot Framework
        remote library server, imported with the
        [https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#remote-library-interface|Remote]
        library. ``path`` is usually a ``TcpNetwork`` driver whose exporter
        reaches the service. ``local_port=0`` picks a free port.

        The forward is a driver context: it stays open until `Close Driver
        Context` (by ``alias``, default ``port_forward``), the end of
        ``scope``, or until its session disconnects.

        | ${address}= | Open Port Forward | dut.remote |
        | Import Library | Remote | http://${address} | AS | Board |
        """
        if scope.lower() not in context.SCOPES:
            raise Failure(f"scope must be one of {', '.join(context.SCOPES)}, not '{scope}'")
        try:
            from jumpstarter_driver_network.adapters import TcpPortforwardAdapter
        except ImportError:
            raise Failure(
                "Open Port Forward needs jumpstarter-driver-network: pip install jumpstarter-driver-network"
            ) from None
        with sessions.lock(), mapped_errors("Open Port Forward"):
            _check_alias_free(alias)
            target, node = resolve(path, session)
            stack = ExitStack()
            try:
                host, port = stack.enter_context(
                    TcpPortforwardAdapter(client=node, method=method, local_port=int(local_port))
                )
            except BaseException:
                stack.close()
                raise
            entry = _register(alias or None, path, "port_forward", target, f"{host}:{port}", stack, scope)
            logger.info(f"forwarding {host}:{port} to {path} ({method}), driver context '{entry.alias}'")
            return entry.value

    @keyword
    def close_driver_context(self, alias: str | None = None) -> None:
        """Exit an open driver context; without ``alias``, the most recently opened one.

        Fails if no such context is open, or if exiting it fails.

        | Close Driver Context | adb |
        | Close Driver Context |
        """
        with mapped_errors("Close Driver Context"):
            entry = _lookup(alias)
            entry.close()

    @keyword
    def close_all_driver_contexts(self) -> None:
        """Exit every open driver context, most recently opened first.

        Every context is closed even when one fails; the first failure is
        reported afterwards.

        | [Teardown] | Close All Driver Contexts |
        """
        failure: BaseException | None = None
        for entry in reversed(open_contexts()):
            try:
                with mapped_errors("Close All Driver Contexts"):
                    entry.close()
            except Exception as e:  # noqa: BLE001 - close the rest, report the first
                logger.warn(f"closing driver context '{entry.alias}' failed: {e}")
                failure = failure or e
        if failure is not None:
            raise failure

    @keyword
    def get_driver_context_value(self, alias: str | None = None) -> Any:
        """Return what an open driver context yielded, as `Open Driver Context` returned it.

        Without ``alias``, the most recently opened context.

        | ${forward}= | Get Driver Context Value | adb |
        """
        return _lookup(alias).value

    @keyword
    def collect_driver_stream(
        self,
        path: str,
        method: str,
        *args: Any,
        count: int | None = None,
        timeout: str | None = "10s",
        driver_method: str | None = None,
        session: str | None = None,
    ) -> list[Any]:
        """Read a streaming driver call for ``count`` items or ``timeout``, then cancel it.

        Returns the items read, a list that is shorter than ``count`` when
        ``timeout`` (a Robot time string, default 10 seconds) passes first.
        The stream is cancelled on the exporter too, so endless streams such
        as power readings stop. ``timeout=None`` waits for ``count`` items
        however long they take; giving neither fails.

        This calls the driver's exported streaming method directly, so the
        items are the values as the driver sent them: models arrive as
        dictionaries (usable as ``${item.voltage}``), not as the client's
        model classes, and client-side processing in the client method is
        skipped. ``method`` names the exported driver method; use
        ``driver_method`` when it differs from the client method's name.
        Positional ``args`` are passed to it as given, without the type
        conversion driver keywords get, so write numbers as ``${5}``.

        | ${readings}= | Collect Driver Stream | dut.power | read | count=5 |
        | Should Be True | ${readings[0].voltage} >= 0 |
        | ${all}= | Collect Driver Stream | dut.power | read | timeout=3s |
        """
        limit, seconds = _stream_limits(count, timeout)
        name = driver_method or method
        with sessions.lock(), mapped_errors("Collect Driver Stream"):
            _, node = resolve(path, session)
            if limit == 0:
                return []
            items = _call_cancellable(node.portal, _collect, node, name, args, limit, seconds)
            if limit is not None and len(items) < limit:
                logger.info(f"{path}.{name}: timed out after {len(items)} of {limit} items")
            return [_robot_value(item, stem=name) for item in items]

    @keyword
    def get_driver_property(self, path: str, name: str, session: str | None = None) -> Any:
        """Return a property of the driver client at ``path``.

        Works for properties, cached properties and plain attributes, for
        values that a client computes or caches rather than a method call.
        Use `Call Driver` for methods.

        | ${user}= | Get Driver Property | dut.ssh | username |
        """
        with sessions.lock(), mapped_errors("Get Driver Property"):
            _, node = resolve(path, session)
            try:
                value = getattr(node, name)
            except AttributeError as e:
                raise Failure(f"driver client {type(node).__name__} at '{path}' has no property '{name}': {e}") from e
            if inspect.ismethod(value):
                raise Failure(f"'{name}' of the driver at '{path}' is a method; use Call Driver | {path} | {name}")
            return _robot_value(value, stem=name)
