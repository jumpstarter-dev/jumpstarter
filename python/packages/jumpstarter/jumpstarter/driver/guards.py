"""Instance-level call guards: let a driver refuse calls to *specific* driver instances.

``add_guard(driver, guard)`` makes every exported call on that one driver
instance (unary, streaming, and stream opens) consult ``guard(method)`` first:
it returns ``None`` to allow the call, or a reason, which fails the call with
``FAILED_PRECONDITION``. All callers reach a driver through these gRPC entry
points: clients, agents, and lease hook scripts alike.

This is opt-in and narrow on purpose. Nothing changes on the ``Driver`` class or
on any driver that no guard was added to: the three entry points are shadowed
by instance attributes on the guarded instance only, and each wrapper only
consults the guards before delegating to the original bound method.

Typical uses: a flasher refusing ``off`` on the DUT's power while writing, or a
driver refusing calls to itself while its own script runs.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from grpc import StatusCode

from jumpstarter.common.streams import DriverStreamRequest

Guard = Callable[[str], str | None]
_ATTR = "_jmp_call_guards"


async def _check(target: Any, guards: list[Guard], method: str, context) -> None:
    for guard in guards:
        reason = guard(method)
        if reason:
            target.logger.warning("Refused %s: %s", method, reason)
            await context.abort(StatusCode.FAILED_PRECONDITION, reason)


def add_guard(target: Any, guard: Guard) -> None:
    """Install ``guard`` on one driver instance. ``guard(method)`` returns a refusal reason or ``None``.

    Guards must be fast and must not raise. Adding the same guard twice is a no-op.
    """
    existing: list[Guard] | None = target.__dict__.get(_ATTR)
    if existing is not None:
        if guard not in existing:
            existing.append(guard)
        return

    guards: list[Guard] = [guard]
    # Wrap this instance's gRPC entry points, once.
    original_call = target.DriverCall
    original_streaming_call = target.StreamingDriverCall
    original_stream = target.Stream

    async def DriverCall(request, context):
        await _check(target, guards, request.method, context)
        return await original_call(request, context)

    async def StreamingDriverCall(request, context):
        await _check(target, guards, request.method, context)
        async for response in original_streaming_call(request, context):
            yield response

    @asynccontextmanager
    async def Stream(request, context):
        if isinstance(request, DriverStreamRequest):  # resource uploads touch no hardware
            await _check(target, guards, request.method, context)
        async with original_stream(request, context) as stream:
            yield stream

    # object.__setattr__: instance attributes shadow the class methods for this
    # instance only, without going through any model validation.
    object.__setattr__(target, "DriverCall", DriverCall)
    object.__setattr__(target, "StreamingDriverCall", StreamingDriverCall)
    object.__setattr__(target, "Stream", Stream)
    object.__setattr__(target, _ATTR, guards)


def guards_of(target: Any) -> list[Guard]:
    """The guards installed on ``target`` (empty if it was never guarded)."""
    return list(target.__dict__.get(_ATTR, []))
