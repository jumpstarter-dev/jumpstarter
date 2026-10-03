"""Driver scripts: ``j`` scripts declared in a driver's config, run by the driver like lease hooks.

Some drivers need a site-specific procedure that can't be expressed as fixed
configuration: entering a boot mode through an unusual power arrangement, a
vendor serial dialogue, conditional logic across several drivers. A driver
opts in by adding a :class:`DriverScript` field to its config and mixing in
:class:`ScriptRunnerMixin`; it then calls ``await self.run_script(cfg)``.

The script runs on the exporter with access to **every** driver in the
exporter, exactly like a lease hook: the driver serves a private Unix socket
over the exporter's live driver tree for the script's duration and points
``JUMPSTARTER_HOST`` at it, so ``j <path> <command>`` (bash) and the Python
``jumpstarter.utils.env.env()`` client both work, with the paths from the
exporter config. The private session is never entered, so it does not reset or
close any driver.

Environment for the script:

``JMP_DRIVER_PATH``
    The declaring driver's own ``j`` path (e.g. ``bench dut1``), so a script can
    reach the driver's children portably: ``j $JMP_DRIVER_PATH power on``.
    Taken from ``self_path`` in the script's config when set, otherwise found in
    the exporter tree.
``JUMPSTARTER_HOST``, ``JMP_DRIVERS_ALLOW``
    How ``j`` and ``env()`` reach the drivers (as for hooks).
plus anything in the script config's ``env`` and the driver's own extras.

Example driver config (YAML)::

    my_procedure:
      script: |
        set -e
        j pdu outlet3 off
        j $JMP_DRIVER_PATH relay on
      exec: /bin/bash        # optional, as for hooks: .py files use the exporter's Python
      timeout: 60            # seconds, as for hooks
      block_self: true       # refuse calls to this driver while the script runs

Scripts run with the same runner and environment as lease hooks
(``run_script`` and ``script_env`` in ``jumpstarter.exporter.hooks``:
interpreter detection, PTY line buffering, timeout handling), so a driver script
behaves exactly like a hook. Nothing here changes the ``Driver`` class or any
other driver.
"""

from __future__ import annotations

import os
import re
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from jumpstarter.common import ExporterStatus
from jumpstarter.driver.guards import add_guard

_ROOT_ATTR = "_jmp_exporter_root"
_RUNNING_ATTR = "_jmp_scripts_running"
_TAIL_LINES = 20

_SCRIPT_CALLER: ContextVar[Any] = ContextVar("jmp_script_caller", default=None)


def script_caller() -> Any | None:
    """The driver whose own script made the call being handled, or ``None`` for any other caller.

    Lets a guard tell a driver's own scripts apart from everyone else: a flasher
    can let its entry script switch power while a job runs, and still refuse
    the same call from the lease holder.
    """
    return _SCRIPT_CALLER.get()


_SESSION_CLASS: Any = None


def _script_session_class() -> Any:
    """A ``Session`` that tags every call it dispatches with the script's driver (see :func:`script_caller`)."""
    global _SESSION_CLASS
    if _SESSION_CLASS is None:
        from jumpstarter.exporter.session import Session

        class ScriptSession(Session):
            owner: Any = None  # set after construction; Session keeps its own __init__

            async def DriverCall(self, request, context):
                token = _SCRIPT_CALLER.set(self.owner)
                try:
                    return await super().DriverCall(request, context)
                finally:
                    _SCRIPT_CALLER.reset(token)

            async def StreamingDriverCall(self, request, context):
                token = _SCRIPT_CALLER.set(self.owner)
                try:
                    async for value in super().StreamingDriverCall(request, context):
                        yield value
                finally:
                    _SCRIPT_CALLER.reset(token)

            async def Stream(self, request_iterator, context):
                token = _SCRIPT_CALLER.set(self.owner)
                try:
                    return await super().Stream(request_iterator, context)
                finally:
                    _SCRIPT_CALLER.reset(token)

        _SESSION_CLASS = ScriptSession
    return _SESSION_CLASS


class DriverScript(BaseModel):
    """A ``j`` script declared in a driver's config: ``script``, ``exec`` and ``timeout`` as for lease hooks."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    script: str = Field(description="Inline script text, or the path of a script file on the exporter.")
    exec_: str | None = Field(
        default=None,
        alias="exec",
        description=(
            "Interpreter, e.g. /bin/bash or python3. When unset, as for hooks: "
            "a .py file runs with the exporter's Python, anything else with /bin/sh."
        ),
    )
    timeout: int = Field(default=120, ge=1, description="Seconds before the script is terminated.")
    block_self: bool = Field(
        default=True,
        description=(
            "Refuse calls to the declaring driver while the script runs (prevents re-entry and deadlocks), "
            "except the methods the driver lists as safe for its scripts."
        ),
    )
    self_path: str | None = Field(
        default=None,
        description="The declaring driver's j path (dotted or space separated); found in the exporter if unset.",
    )
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for the script.")


@dataclass(frozen=True)
class ScriptResult:
    output: str
    """What the script printed (stdout and stderr together)."""


class ScriptError(RuntimeError):
    """A driver script failed or timed out."""


def is_proxy(node: Any) -> bool:
    """True for a ``ref:`` proxy (``jumpstarter_driver_composite.driver.Proxy``)."""
    return type(node).__name__ == "Proxy" and hasattr(type(node), "_resolve_proxy_target")


def tree_path(root: Any, target: Any) -> list[str] | None:
    """The config names leading from ``root`` to ``target`` (``[]`` for the root itself)."""
    if root is target:
        return []
    stack: list[tuple[Any, list[str]]] = [(root, [])]
    while stack:
        node, path = stack.pop()
        for name, child in node.children.items():
            if child is target:
                return [*path, name]
            if not is_proxy(child):
                stack.append((child, [*path, name]))
    return None


class ScriptRunnerMixin:
    """Opt-in for drivers that run :class:`DriverScript`\\ s. Mix in before ``Driver``.

    Captures the exporter's driver tree when the exporter enumerates it (before
    it resets anything), so scripts can reach every driver.

    With ``block_self``, a script can't call its own driver, except the methods
    in ``script_callable_methods``: list read-only methods there that don't wait
    on anything the script's caller holds (a lock, say), so scripts can use them
    to check on the hardware, e.g. ``j $JMP_DRIVER_PATH wait-present``.
    """

    script_callable_methods: ClassVar[frozenset[str]] = frozenset()

    def enumerate(self, *, root=None, parent=None, name=None):
        result = super().enumerate(root=root, parent=parent, name=name)  # ty: ignore[unresolved-attribute]
        object.__setattr__(self, _ROOT_ATTR, root if root is not None else self)
        return result

    @property
    def exporter_root(self) -> Any | None:
        """The exporter's root driver, once the exporter has enumerated its tree (else ``None``)."""
        return self.__dict__.get(_ROOT_ATTR)

    def script_running(self) -> bool:
        return self.__dict__.get(_RUNNING_ATTR, 0) > 0

    def _self_path(self, cfg: DriverScript) -> str:
        if cfg.self_path:
            return " ".join(p for p in re.split(r"[.\s]+", cfg.self_path) if p)
        return " ".join(tree_path(self.exporter_root or self, self) or [])

    def _install_self_block(self) -> None:
        def guard(method: str) -> str | None:
            if method in self.script_callable_methods:
                return None
            if self.script_running():
                return f"{type(self).__name__} is running its script; calls to it are refused until it finishes"
            return None

        if "_jmp_self_block_guard" not in self.__dict__:
            object.__setattr__(self, "_jmp_self_block_guard", guard)
            add_guard(self, guard)

    def _script_env(self, cfg: DriverScript, socket_path: str, extra: dict[str, str] | None) -> dict[str, str]:
        from jumpstarter.exporter.hooks import script_env

        env = script_env(socket_path, {"JMP_DRIVER_PATH": self._self_path(cfg), **cfg.env, **(extra or {})})
        # The exporter's own environment first, so `j` and `python3` are the exporter's
        # even when it was started without activating its virtualenv.
        env["PATH"] = os.pathsep.join([os.path.dirname(sys.executable), env.get("PATH", "")])
        return env

    async def run_script(self, cfg: DriverScript, *, env: dict[str, str] | None = None) -> ScriptResult:
        """Run ``cfg`` with ``j`` access to the exporter's drivers, with the lease hook runner.

        Raises :class:`ScriptError` if the script exits non-zero, times out, or can't start.
        """
        from jumpstarter.exporter.hooks import run_script

        if cfg.block_self:
            self._install_self_block()
        # Served over the live tree, never entered: no driver is reset or closed. Its
        # calls are tagged with this driver, so guards can recognize them (script_caller).
        session = _script_session_class()(root_device=self.exporter_root or self, exporter_name="driver-script")
        session.owner = self
        session.update_status(ExporterStatus.LEASE_READY, "driver script")
        object.__setattr__(self, _RUNNING_ATTR, self.__dict__.get(_RUNNING_ATTR, 0) + 1)
        try:
            async with session.serve_unix_async() as socket_path:
                result = await run_script(
                    cfg.script,
                    env=self._script_env(cfg, str(socket_path), env),
                    timeout=cfg.timeout,
                    exec_=cfg.exec_,
                    label="Script",
                )
        finally:
            object.__setattr__(self, _RUNNING_ATTR, self.__dict__.get(_RUNNING_ATTR, 1) - 1)
        output = "\n".join(result.output)
        if result.error is not None:
            hint = ""
            if result.returncode == 127:
                hint = " (command not found: is `j` from jumpstarter-cli on the exporter's PATH?)"
            tail = "\n".join(result.output[-_TAIL_LINES:])
            raise ScriptError(result.error + hint + (f":\n{tail}" if tail else "")) from result.cause
        return ScriptResult(output=output)
