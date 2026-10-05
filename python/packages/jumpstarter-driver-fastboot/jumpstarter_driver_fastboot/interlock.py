"""Interlocks: protect the device from power-offs and resets while it is being flashed.

Cutting power, pressing a reset/volume button, or typing on the bootloader
console mid-flash can brick a device. While a flash job is active a flasher
protects:

* its own hardware children (and the real drivers behind their ``ref:``), and
* power drivers in the exporter, scoped by the driver's ``interlock_power``
  option: all of them by default, or the listed driver paths, so that on an
  exporter powering several DUTs only the flashed DUT's power is held.

Two paths are covered:

* **Calls**, from any caller (lease holder, agents, lease hook scripts), go
  through a driver's gRPC entry points (``DriverCall``, ``StreamingDriverCall``,
  ``Stream``). Everything except read-only methods is refused with
  ``FAILED_PRECONDITION``.
* **Lifecycle** methods the exporter calls directly: ``reset()`` at session
  start is skipped, and ``close()`` at session end is deferred until the flash
  is safe, then run. Some power drivers switch the DUT off in these (Tasmota
  does in both, Ykush with ``default: off`` on reset), and deferring rather
  than skipping ``close()`` keeps resource release (a GPIO line request, say)
  intact.

Scope is deliberately narrow: nothing in the core ``Driver`` class or any other
driver's code changes. Calls are guarded with the core, opt-in
``jumpstarter.driver.guards``; only the protected *instances* get their entry
points (and here ``reset``/``close``) shadowed, and each wrapper only consults
the guards before delegating to the original bound method. An exporter without a
flasher is unaffected.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from jumpstarter.driver.guards import add_guard, guards_of
from jumpstarter.driver.scripts import is_proxy

__all__ = ["add_guard", "guards_of", "is_lifecycle_protected", "protect_lifecycle", "resolve_path", "walk"]

Busy = Callable[[], str | None]
_LIFECYCLE_ATTR = "_jmp_flash_lifecycle_busy"


def protect_lifecycle(target: Any, busy: Busy, *, poll_interval: float = 0.2) -> None:  # noqa: C901 - closures
    """Skip ``reset()`` and defer ``close()`` on ``target`` while ``busy()`` returns a reason."""
    checks: list[Busy] | None = target.__dict__.get(_LIFECYCLE_ATTR)
    if checks is not None:
        if busy not in checks:
            checks.append(busy)
        return

    active: list[Busy] = [busy]
    original_reset = target.reset
    original_close = target.close

    def reason() -> str | None:
        return next((r for r in (check() for check in active) if r), None)

    def reset() -> None:
        why = reason()
        if why:
            target.logger.warning("Not resetting during a flash (%s)", why)
            return
        original_reset()

    def close() -> None:
        why = reason()
        if not why:
            original_close()
            return
        target.logger.warning("Deferring close until the flash is safe (%s)", why)

        def wait_then_close() -> None:
            while reason():
                time.sleep(poll_interval)
            try:
                original_close()
            except Exception:
                target.logger.warning("Deferred close failed", exc_info=True)

        threading.Thread(target=wait_then_close, name="flash-deferred-close", daemon=True).start()

    object.__setattr__(target, "reset", reset)
    object.__setattr__(target, "close", close)
    object.__setattr__(target, _LIFECYCLE_ATTR, active)


def is_lifecycle_protected(target: Any) -> bool:
    return _LIFECYCLE_ATTR in target.__dict__


def walk(root: Any) -> Iterator[Any]:
    """Every real driver instance in a tree, skipping ``ref:`` proxies (their targets are in the tree)."""
    stack = list(root.children.values())
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen or is_proxy(node):
            continue
        seen.add(id(node))
        yield node
        stack.extend(node.children.values())


def resolve_path(root: Any, path: str) -> Any:
    """The driver at a dotted exporter path (e.g. ``pdu.outlet3``), following ``ref:`` proxies.

    Proxies are followed by their configured ``ref`` from the root, so this works
    before the exporter has finished resolving them. Raises ``KeyError`` if the
    path does not exist.
    """
    seen: set[str] = set()
    node = root
    for part in path.split("."):
        node = node.children[part]
        while is_proxy(node):
            ref = node.ref
            if ref in seen:
                raise KeyError(f"ref cycle at {ref!r}")
            seen.add(ref)
            node = node._proxy_target or resolve_path(root, ref)
    return node
