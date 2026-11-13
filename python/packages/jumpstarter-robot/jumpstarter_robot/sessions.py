"""Connections to exporters, shared by every library instance in a Robot run.

A session is one connected driver tree: the ``jmp shell`` the run is inside, a
lease acquired from a controller, or a local exporter config served in this
process. Sessions are named so a run can hold several (``android`` and
``ios``, or a DUT and a simulator). Library instances find them here by name,
which is why the registry is module level rather than per library instance.

A session is usually opened lazily: the ``Jumpstarter`` library registers how
to open it, and the first keyword that needs it opens it.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from anyio.from_thread import start_blocking_portal

from .configs import load_client_config, load_exporter_config
from .errors import JumpstarterRobotError
from jumpstarter.client import DriverClient, client_from_path
from jumpstarter.client.base import resolve_root_child
from jumpstarter.client.selectors import parse_label_selector
from jumpstarter.config.env import JMP_LEASE, JUMPSTARTER_HOST
from jumpstarter.utils.env import env_with_metadata

logger = logging.getLogger(__name__)

DEFAULT_SESSION = "default"


class LeaseEnded(JumpstarterRobotError):
    """The session's lease ended, was transferred, or was revoked."""

    def __init__(self, message: str, session: str | None = None):
        super().__init__(message)
        self.session = session


class NoSession(JumpstarterRobotError):
    """No Jumpstarter session is open or configured."""


@dataclass(eq=False)
class Session:
    """One connected driver tree and everything that keeps it open."""

    name: str
    kind: str  # shell | lease | local | client
    client: DriverClient
    exporter: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    lease_name: str | None = None
    owns_lease: bool = False
    release_grace: float = 0.0
    lease: Any = None
    _stack: ExitStack = field(default_factory=ExitStack)
    _closers: list[Callable[[], None]] = field(default_factory=list)
    _closed: bool = False

    @classmethod
    def from_shell(cls, name: str = DEFAULT_SESSION) -> Session:
        """Use the ``jmp shell`` this run was started from (``JUMPSTARTER_HOST``)."""
        stack = ExitStack()
        try:
            client, metadata = stack.enter_context(env_with_metadata())
        except BaseException:
            stack.close()
            raise
        return cls(
            name=name,
            kind="shell",
            client=client,
            exporter=metadata.name,
            labels=dict(metadata.labels),
            lease_name=metadata.lease,
            _stack=stack,
        )

    @classmethod
    def from_lease(
        cls,
        name: str = DEFAULT_SESSION,
        *,
        config: str | None = None,
        config_dir: Path | None = None,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
        acquisition_timeout: timedelta | None = None,
        allow_disabled: bool = False,
        release_grace: float = 1.0,
        reuse_lease: bool = True,
        acquire: bool = True,
    ) -> Session:
        """Acquire a lease (or attach to ``lease_name``) and connect to its exporter.

        Attaching to an existing lease never releases it; a lease this session
        created is released when the session closes. Note that a ``JMP_LEASE``
        environment variable makes the controller client attach to that lease.

        With ``reuse_lease`` and no ``lease_name``, an active lease of this
        client for exactly the same ``selector`` (and ``exporter_name``) is
        attached to instead of acquiring a new one, so a lease created with
        ``jmp create lease`` serves run after run.

        Without ``acquire`` the session only attaches: to ``lease_name``,
        ``JMP_LEASE`` or a matching active lease, and fails when there is none.
        """
        client_config = load_client_config(config, base_dir=config_dir)
        # JMP_LEASE already names the lease to attach to
        named = bool(lease_name or os.environ.get(JMP_LEASE))
        reuse = reuse_lease and not named and bool(selector or exporter_name)
        stack = ExitStack()
        try:
            portal = stack.enter_context(start_blocking_portal())
            reused = _reusable_lease(portal, client_config, selector, exporter_name) if reuse else None
            if reused is None and not named and not acquire:
                raise NoSession(_no_lease(name, client_config.metadata.name, selector, exporter_name, reuse))
            if reused is not None:
                _announce(f"Jumpstarter: reusing lease {reused} for session '{name}'")
                # the controller compares the selector as text; the match is already exact
                lease_name, selector = reused, None
            lease = stack.enter_context(
                portal.wrap_async_context_manager(
                    client_config.lease_async(
                        selector or None,
                        exporter_name or None,
                        lease_name or None,
                        duration,
                        portal,
                        acquisition_timeout=acquisition_timeout,
                        allow_disabled=allow_disabled,
                    )
                )
            )
            stack.enter_context(lease.monitor())
            client = stack.enter_context(lease.connect())
        except BaseException:
            _abandon(stack)
            raise
        return cls(
            name=name,
            kind="lease",
            client=client,
            exporter=lease.exporter_name,
            labels=dict(lease.exporter_labels),
            lease_name=lease.name,
            owns_lease=bool(lease.release),
            release_grace=release_grace,
            lease=lease,
            _stack=stack,
        )

    @classmethod
    def from_exporter_config(
        cls, name: str = DEFAULT_SESSION, *, config: str, config_dir: Path | None = None
    ) -> Session:
        """Serve a local exporter config in this process, like ``jmp shell --exporter-config``.

        The config is local and therefore trusted, as it is for ``jmp shell``,
        so every driver client it names may be loaded.
        """
        exporter = load_exporter_config(config, base_dir=config_dir)
        stack = ExitStack()
        try:
            portal = stack.enter_context(start_blocking_portal())
            path = stack.enter_context(portal.wrap_async_context_manager(exporter.serve_unix_async()))
            client = stack.enter_context(
                portal.wrap_async_context_manager(client_from_path(path, portal, stack, allow=[], unsafe=True))
            )
        except BaseException:
            _abandon(stack)
            raise
        return cls(
            name=name,
            kind="local",
            client=client,
            exporter=exporter.metadata.name or getattr(exporter, "alias", None) or "local",
            labels={},
            _stack=stack,
        )

    @classmethod
    def from_client(
        cls, client: DriverClient, name: str = DEFAULT_SESSION, *, exporter: str = "test", labels=None
    ) -> Session:
        """Wrap an already connected client; used by tests and embedding code."""
        return cls(name=name, kind="client", client=client, exporter=exporter, labels=dict(labels or {}))

    @property
    def alive(self) -> bool:
        lease = self.lease
        if lease is None:
            return not self._closed
        revoked = getattr(lease, "lease_revoked", False)
        return not (self._closed or lease.lease_ended or lease.lease_transferred or revoked)

    def check_alive(self) -> None:
        if self._closed:
            raise LeaseEnded(f"session '{self.name}' is closed", self.name)
        lease = self.lease
        if lease is None:
            return
        if lease.lease_transferred:
            raise LeaseEnded(f"lease {self.lease_name} was transferred to another client", self.name)
        if getattr(lease, "lease_revoked", False):
            raise LeaseEnded(f"lease {self.lease_name} was revoked", self.name)
        if lease.lease_ended:
            raise LeaseEnded(f"lease {self.lease_name} has ended", self.name)

    def resolve(self, path: str) -> DriverClient:
        """Find the driver at a dotted tree path, positionally through ``children``."""
        segments = [s for s in path.split(".") if s]
        if not segments:
            return self.client
        try:
            node = resolve_root_child(self.client, segments[0])
        except KeyError:
            raise KeyError(_missing(path, segments[0], self.client)) from None
        walked = [segments[0]]
        for segment in segments[1:]:
            children = getattr(node, "children", {})
            if segment not in children:
                raise KeyError(_missing(path, ".".join(walked + [segment]), node))
            node = children[segment]
            walked.append(segment)
        return node

    def on_close(self, closer: Callable[[], None]) -> None:
        """Run ``closer`` before the session's connection goes away."""
        self._closers.append(closer)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while self._closers:
            closer = self._closers.pop()
            try:
                closer()
            except Exception:
                logger.warning("error while closing a resource of session %s", self.name, exc_info=True)
        try:
            self._stack.close()
        finally:
            if self.owns_lease and self.release_grace > 0:
                # The exporter needs a moment to see the release before the
                # next lease of the same board; jumpstarter-testing does the same.
                time.sleep(self.release_grace)


def _abandon(stack: ExitStack) -> None:
    """Unwind a session that failed or was interrupted (an RF timeout, Ctrl-C) while opening.

    The exception goes to the stack so the blocking portal cancels its
    remaining tasks: an interrupted enter leaves its task running in the
    portal (still acquiring the lease, or starting the exporter), and a portal
    stopped without cancelling waits for that task forever.
    """
    stack.__exit__(*sys.exc_info())


def _announce(message: str) -> None:
    """Log ``message``, and show it on the Robot console during a run."""
    from robot.running.context import EXECUTION_CONTEXTS

    logger.info(message)
    if EXECUTION_CONTEXTS.current is not None:
        from robot.api import logger as robot_logger

        robot_logger.console(message)


def _selector_key(selector: str | None) -> tuple[dict[str, str], set[tuple[str, str, frozenset[str]]]]:
    """A label selector in a form where equivalent selectors compare equal.

    Requirement order and value order do not matter, and ``a!=x,a!=y`` is
    ``a notin (x,y)``, which is how the controller reports it.
    """
    labels, expressions = parse_label_selector(selector or "")
    excluded: dict[str, set[str]] = {}
    requirements: set[tuple[str, str, frozenset[str]]] = set()
    for key, operator, values in expressions:
        if operator in ("!=", "notin"):
            excluded.setdefault(key, set()).update(values)
        else:
            requirements.add((key, operator, frozenset(values)))
    requirements.update((key, "notin", frozenset(values)) for key, values in excluded.items())
    return labels, requirements


def matching_lease(leases: Iterable[Any], client: str, selector: str | None, exporter_name: str | None) -> Any | None:
    """The active lease ``client`` may use for exactly ``selector`` and ``exporter_name``, if any.

    A lease for a narrower or wider selector never matches. Of several, the
    most recently started wins, then the first by name.
    """
    wanted = _selector_key(selector)
    candidates = [
        lease
        for lease in leases
        if lease.is_accessible_by(client)
        and not lease.effective_end_time
        and _selector_key(lease.selector) == wanted
        and (not exporter_name or lease.exporter_name == exporter_name)
    ]
    if not candidates:
        return None
    chosen = min(candidates, key=lambda lease: (-_begin(lease), lease.name))
    if len(candidates) > 1:
        others = ", ".join(sorted(lease.name for lease in candidates if lease is not chosen))
        _announce(f"Jumpstarter: several leases match; using {chosen.name}, not {others}")
    return chosen


def _begin(lease: Any) -> float:
    begin = lease.effective_begin_time
    return begin.timestamp() if begin is not None else float("-inf")


def _reusable_lease(portal: Any, client_config: Any, selector: str | None, exporter_name: str | None) -> str | None:
    """The name of an active lease of this client to attach to instead of acquiring one."""
    try:
        # list_leases runs its own event loop unless called inside one, so it runs in the portal
        listed = portal.call(partial(client_config.list_leases, only_active=True))
    except Exception as e:  # noqa: BLE001 - reuse only saves a lease; acquiring one still works
        logger.warning("cannot list active leases to find one to reuse: %s", e)
        return None
    # A lease another session of this run already uses is not free to share:
    # both sessions would drive one board, and the one that acquired it would
    # release it from under the other.
    with _lock:
        held = {s.lease_name for s in _sessions.values() if not s._closed and s.lease_name}
    free = [lease for lease in listed.leases if lease.name not in held]
    lease = matching_lease(free, client_config.metadata.name, selector, exporter_name)
    return lease.name if lease is not None else None


def _no_lease(name: str, client: str, selector: str | None, exporter_name: str | None, looked: bool) -> str:
    """Why session ``name`` found no lease to attach to, and the three ways to get one."""
    create = f"jmp create lease -l {selector} --duration 1h" if selector else "jmp create lease"
    ways = f"create one (`{create}`), run inside `jmp shell`, or call `Acquire Lease`"
    if not looked:
        return f"session '{name}' does not look for a lease (lease reuse is off): name one, or call `Acquire Lease`"
    wanted = selector or f"exporter {exporter_name}"
    return f"session '{name}': no active lease of client '{client}' matches {wanted}; {ways}"


def _missing(path: str, at: str, node: Any) -> str:
    available = sorted(getattr(node, "children", {}))
    return f"driver '{at}' (from path '{path}') is not present on this exporter; available: {available}"


SessionFactory = Callable[[], Session]

# How leases and local exporters are opened; tests replace these
# (see jumpstarter_robot.testing) to run without a controller.
open_lease: Callable[..., Session] = Session.from_lease
open_local: Callable[..., Session] = Session.from_exporter_config
open_shell: Callable[..., Session] = Session.from_shell

_lock = threading.RLock()
_sessions: dict[str, Session] = {}
_factories: dict[str, SessionFactory] = {}
_default = DEFAULT_SESSION


def lock() -> threading.RLock:
    """The lock every keyword holds while it talks to a session."""
    return _lock


def register_factory(name: str, factory: SessionFactory) -> None:
    """Record how to open session ``name`` the first time a keyword needs it."""
    with _lock:
        _factories[name] = factory


def unregister_factory(name: str, factory: SessionFactory | None = None) -> None:
    with _lock:
        # == rather than is: a bound method such as ``config.open`` is a new object on every access
        if factory is None or _factories.get(name) == factory:
            _factories.pop(name, None)


def install(session: Session) -> Session:
    """Make an opened session available by its name, replacing a closed one."""
    with _lock:
        current = _sessions.get(session.name)
        if current is not None and current is not session and not current._closed:
            raise JumpstarterRobotError(f"session '{session.name}' is already open; release it first")
        _sessions[session.name] = session
        return session


def opened(name: str | None = None) -> Session | None:
    with _lock:
        session = _sessions.get(name or _default)
        return session if session is not None and not session._closed else None


def get(name: str | None = None) -> Session:
    """Return session ``name``, opening it from its registered factory if needed.

    With nothing registered, a run started from ``jmp shell`` uses the shell.
    """
    name = name or _default
    with _lock:
        session = opened(name)
        if session is not None:
            return session
        factory = _factories.get(name)
        if factory is None:
            if os.environ.get(JUMPSTARTER_HOST):
                factory = lambda: open_shell(name)  # noqa: E731
            else:
                raise NoSession(
                    f"no Jumpstarter session '{name}': run inside `jmp shell`, attach to a lease you created "
                    "(JMP_LEASE), call Acquire Lease, or serve an exporter config (Start Local Exporter)"
                )
        return install(factory())


def close(name: str | None = None) -> bool:
    """Close session ``name`` if open; returns whether one was closed."""
    with _lock:
        session = _sessions.pop(name or _default, None)
    if session is None or session._closed:
        return False
    session.close()
    return True


def close_all() -> None:
    with _lock:
        names = list(_sessions)
    for name in names:
        close(name)


def names() -> list[str]:
    with _lock:
        return sorted(set(_sessions) | set(_factories))


def default_name() -> str:
    return _default


# Switch Session lasts for the suite that called it (and its children), like
# Set Library Search Order: (suite id, the default it replaced), innermost last.
_switches: list[tuple[str | None, str]] = []


def default_switched() -> bool:
    """Whether `Switch Session` chose the default session explicitly."""
    return bool(_switches)


def set_default(name: str, suite: str | None = None) -> str:
    """Make ``name`` the session keywords use when none is given; returns the previous one.

    With ``suite``, the switch ends with that suite (see `end_suite`);
    without, it lasts until `reset`.
    """
    global _default
    with _lock:
        previous, _default = _default, name
        _switches.append((suite, previous))
        return previous


def end_suite(suite: str) -> None:
    """Undo the switches ``suite`` made; idempotent, as several listeners call it."""
    global _default
    with _lock:
        for index, (owner, previous) in enumerate(_switches):
            if owner == suite:
                _default = previous
                del _switches[index:]
                return


# Config of declared sessions by name, so Acquire Lease for a session declared
# by a Session library uses its client config, duration and the rest.
_configs: dict[str, Any] = {}


def register_config(name: str, config: Any) -> None:
    with _lock:
        _configs[name] = config


def unregister_config(name: str, config: Any) -> None:
    with _lock:
        if _configs.get(name) is config:
            _configs.pop(name, None)


def registered_config(name: str) -> Any | None:
    """The config session ``name`` was declared with, if any."""
    with _lock:
        return _configs.get(name)


LEASE_END_POLICIES = ("fatal", "fail")
_lease_end_policy = "fatal"
_lease_end_policy_set = False
_session_policies: dict[str, str] = {}


def lease_end_policy(session: str | None = None) -> str:
    """``fatal`` stops the whole run when a lease ends; ``fail`` fails the keyword only.

    A policy declared for ``session`` wins over the run's.
    """
    with _lock:
        return _session_policies.get(session or "", _lease_end_policy)


def set_lease_end_policy(policy: str, session: str | None = None) -> None:
    """Set the run's lease end policy, or with ``session`` that session's.

    Only explicit settings call this, so a later library import without
    ``on_lease_end`` never resets it; a conflicting second setting wins
    with a warning.
    """
    global _lease_end_policy, _lease_end_policy_set
    policy = policy.lower()
    if policy not in LEASE_END_POLICIES:
        raise ValueError(f"on_lease_end must be 'fatal' or 'fail', not '{policy}'")
    with _lock:
        if session is not None:
            previous = _session_policies.get(session)
            _session_policies[session] = policy
            where = f"session '{session}'"
        else:
            previous = _lease_end_policy if _lease_end_policy_set else None
            _lease_end_policy, _lease_end_policy_set = policy, True
            where = "the run"
    if previous is not None and previous != policy:
        logger.warning("on_lease_end for %s changed from '%s' to '%s'", where, previous, policy)


def reset() -> None:
    """Close every session and forget factories, configs, switches and lease end policies."""
    global _default, _lease_end_policy, _lease_end_policy_set
    close_all()
    with _lock:
        _factories.clear()
        _configs.clear()
        _switches.clear()
        _session_policies.clear()
        _default, _lease_end_policy, _lease_end_policy_set = DEFAULT_SESSION, "fatal", False


atexit.register(close_all)
