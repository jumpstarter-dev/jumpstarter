"""``Jumpstarter``: sessions, leases and the curated keyword sets."""

from __future__ import annotations

import os
from html import escape
from typing import Any

from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.libraries.BuiltIn import BuiltIn
from robot.utils import DotDict

from . import context, sessions
from .driver_library import Driver, class_path, instances, shell_methods
from .errors import JumpstarterRobotError, mapped_errors
from .keywords import load_all
from .naming import normalize, title
from .session_library import (
    apply_run_settings,
    dry_run,
    duration_of,
    in_run,
    session_config,
    target_of,
    text,
)
from .signatures import convert_call
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.config.env import JMP_LEASE, JUMPSTARTER_HOST

try:
    from importlib.metadata import version as _version

    __version__ = _version("jumpstarter-robot")
except Exception:  # noqa: BLE001 - running from a source tree
    __version__ = "0"


def walk_tree(client: DriverClient, path: tuple[str, ...] = ()) -> list[tuple[str, DriverClient]]:
    """Every driver below ``client`` as (dotted path, client), parents first."""
    found = []
    for name, child in getattr(client, "children", {}).items():
        child_path = (*path, name)
        found.append((".".join(child_path), child))
        found.extend(walk_tree(child, child_path))
    return found


def _running_keyword() -> bool:
    """Whether a keyword is running (``Import Library``), rather than the suite's own imports."""
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    return current is not None and bool(getattr(current, "steps", None))


def _suite_drivers() -> list[Driver]:
    """The Driver libraries imported into the running suite."""
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    if current is None:
        return []
    # one library imported under two names is one instance
    found = {id(lib): lib for lib in current.namespace.get_library_instances().values() if isinstance(lib, Driver)}
    return list(found.values())


def _visible_drivers() -> list[Driver]:
    """The Driver libraries keywords may act on: the running suite's, or every one outside a run.

    Robot Framework keeps the libraries of earlier suites, which a later one
    that does not import them cannot call.
    """
    from robot.running.context import EXECUTION_CONTEXTS

    return _suite_drivers() if EXECUTION_CONTEXTS.current is not None else instances()


class Jumpstarter:
    """Robot Framework support for Jumpstarter.

    This library connects a Robot Framework run to Jumpstarter exporters and
    provides the keywords that work across drivers: leases, sessions
    and calls to a driver by its path. Each driver's own keywords
    come from a `Driver` library per
    driver, which the generated client resource imports
    (``jmp-robot generate``), or which this library
    imports itself once it is connected.

    = Connecting =

    A session opens the first time a keyword needs it, from the first of
    these that applies:

    1. inside ``jmp shell``: the shell's exporter (``JUMPSTARTER_HOST``)
    2. an existing lease of client config ``config`` (an alias, a client
       config YAML path, or ``env``): ``lease_name`` or ``JMP_LEASE``, else
       the active lease with exactly the same ``selector`` (and
       ``exporter_name``), such as one created with ``jmp create lease``
    3. ``exporter_config``: a local exporter config served in this process,
       like ``jmp shell --exporter-config``

    Opening a session never acquires a lease; with a selector and no
    matching lease it fails. `Acquire Lease` is the keyword that acquires
    one, and `Start Local Exporter` and `Connect To Shell Session` open a
    session explicitly too. Leases `Acquire Lease` acquired are released
    when the run ends or with `Release Lease`; attached leases never are.
    ``reuse_lease=False`` (a pabot pool) stops sessions from attaching to a
    matching lease, so only `Acquire Lease` or a named lease connects them.

    = Lease end =

    ``on_lease_end`` decides what happens when a lease ends, is transferred
    or is revoked during the run: ``fatal`` (the default) stops the run,
    ``fail`` fails only the keyword. It is set for the whole run, by the
    import that gives it; an import without it, such as the one in a
    generated client resource, leaves it as it is. The `Session` library
    takes ``on_lease_end`` for one session.

    = Sessions =

    A session is a named connection, so one run can drive several exporters.
    The generated client resources declare theirs with the `Session`
    library; arguments given here declare the session named ``session``.
    Keywords without a ``session`` argument use the one `Switch Session`
    chose, else the only session declared in the suite, else ``default``.

    = Zero configuration =

    With ``auto_import`` (the default) this library imports a `Driver`
    library for every driver once it connects, so keywords such as
    ``Power On`` appear without a client resource. They are not visible to
    the editor until the run; generate a client resource for IntelliSense.
    """

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_VERSION = __version__
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(
        self,
        exporter_config: str | None = None,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        config: str | None = None,
        session: str = sessions.DEFAULT_SESSION,
        duration: str = "30 minutes",
        acquisition_timeout: str | None = None,
        allow_disabled: bool = False,
        auto_import: bool = True,
        on_lease_end: str | None = None,
        release_grace: str = "1 s",
        reuse_lease: bool = True,
    ):
        self.ROBOT_LIBRARY_LISTENER = self
        self.config = session_config(
            session,
            exporter_config=exporter_config,
            selector=selector,
            exporter_name=exporter_name,
            lease_name=lease_name,
            config=config,
            duration=duration,
            acquisition_timeout=acquisition_timeout,
            allow_disabled=allow_disabled,
            release_grace=release_grace,
            reuse_lease=reuse_lease,
        )
        self.auto_import = auto_import
        if text(on_lease_end):
            # only when given: Robot Framework builds this library for every import,
            # including the ones it then ignores, so a default would reset the policy
            sessions.set_lease_end_policy(str(on_lease_end).strip())
        if self.config.wants_lease or self.config.exporter_config:
            apply_run_settings(self.config)
        self._owned: set[str] = set()
        self._sets = [cls() for cls in load_all()]
        self._names = self._collect_names()
        self._connecting = False  # whether __init__ just connected, or could not
        declares = self._declares = bool(self.config.wants_lease or self.config.exporter_config)
        if declares:
            setattr(self, context.SESSION_MARKER, self.config.name)
        self._declare()
        # A bare import with JMP_LEASE attaches to that lease. It connects at
        # start_suite rather than here: a generated client resource imports this
        # library bare next to the Session that declares its own session, and
        # that suite needs no second connection named ``default``.
        self._attach_env_lease = (
            not declares and not os.environ.get(JUMPSTARTER_HOST) and bool(os.environ.get(JMP_LEASE))
        )
        if declares or os.environ.get(JUMPSTARTER_HOST):
            if self.auto_import and in_run():
                # Connect now so a bad config fails at import. The drivers are
                # imported at start_suite, once the suite's own Driver imports
                # (a generated client resource) are known and can be skipped,
                # or right away when a test imports this library itself.
                self._connecting = True
                opened = None
                with mapped_errors("Jumpstarter"):
                    try:
                        opened = sessions.get(self.config.name)
                    except sessions.NoSession as e:
                        # no lease to attach to yet: `Acquire Lease` connects and imports the drivers
                        robot_logger.warn(f"Jumpstarter: {e}")
                if opened is not None and _running_keyword():
                    self._import_drivers(opened)
            elif self.auto_import and dry_run():
                robot_logger.warn(
                    "Jumpstarter: a dry run does not connect, so driver keywords are not imported; "
                    "import a generated client resource to check them"
                )

    def _declare(self) -> None:
        """Register how this import's session opens, replacing another suite's declaration of the name."""
        from_env = os.environ.get(JUMPSTARTER_HOST) or os.environ.get(JMP_LEASE)
        if not (self._declares or from_env):
            return
        name = self.config.name
        if self._declares:
            sessions.register_config(name, self.config)
            sessions.register_factory(name, self.config.open, self.config.declaration)
        elif sessions.registered_config(name) is None:
            # a bare import never replaces what a Session library declared for the name
            sessions.register_factory(name, self.config.open)
        self._owned.add(name)

    # ---- hybrid library API ----

    def _collect_names(self) -> dict[str, Any]:
        names: dict[str, Any] = {}
        for owner in (self, *self._sets):
            for attr in dir(type(owner)):
                if attr.startswith("_"):
                    continue
                member = getattr(type(owner), attr, None)
                if callable(member) and hasattr(member, "robot_name"):
                    names.setdefault(attr, owner)
        return names

    def get_keyword_names(self) -> list[str]:
        return list(self._names)

    def __getattr__(self, name: str) -> Any:
        names = self.__dict__.get("_names")
        if names is not None and name in names and names[name] is not self:
            return getattr(names[name], name)
        raise AttributeError(name)

    # ---- opening sessions ----

    def _name(self, session: str | None) -> str:
        return text(session) or context.session_name()

    def _open(self, opened: sessions.Session, target: Any = None) -> sessions.Session:
        sessions.install(opened, target=target)
        self._owned.add(opened.name)
        if self.auto_import:
            self._import_drivers(opened)
        return opened

    def _import_drivers(self, session: sessions.Session) -> None:
        """Import a Driver library for every driver of the session not imported yet."""
        if not in_run():
            return
        nodes = [(path, node) for path, node in walk_tree(session.client) if not isinstance(node, StubDriverClient)]
        # Every session opened inside `jmp shell` is the shell, whatever its name.
        imported, taken = set(), set()
        for d in _suite_drivers():
            if session.kind == "shell" or (d.session_name or context.session_name()) == session.name:
                imported.add(d.default_path)
            else:
                taken.add(normalize(d.prefix))
        builtin = BuiltIn()
        for path, node in nodes:
            if path in imported:
                continue
            alias = path if session.name == sessions.DEFAULT_SESSION else f"{session.name}.{path}"
            # Keywords carry the driver's path; a second session's same path gets the session name too
            prefix = title(*path.split("."))
            prefix = title(session.name, prefix) if normalize(prefix) in taken else prefix
            args = [path, f"client={class_path(type(node))}", f"prefix={prefix}", f"session={session.name}"]
            if methods := shell_methods(node):
                args.append(f"methods={','.join(methods)}")
            builtin.import_library("jumpstarter_robot.Driver", *args, "AS", alias)

    # ---- session keywords ----

    @keyword
    def acquire_lease(
        self,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        config: str | None = None,
        duration: str | None = None,
        acquisition_timeout: str | None = None,
        allow_disabled: bool = False,
        session: str | None = None,
        reuse: bool | None = None,
    ) -> str | None:
        """Connect the session to a lease, acquiring one when no existing lease fits.

        Returns the lease name. ``config`` is a client config alias, a path
        to a client config YAML or ``env``; it defaults to the session's
        config, then the environment's client config, then the current
        client. Inside
        ``jmp shell`` the shell's lease is used instead. A ``JMP_LEASE``
        environment variable attaches to that lease.

        Arguments not given come from the session's declaration (this
        library's arguments, or the `Session` library that declares it).
        When that declares only an exporter config, the session serves it
        locally instead, so one suite setup works on the bench and on a lab
        (it then returns no lease name).

        This is the only keyword that acquires a lease: a session opening on
        its own attaches to an existing lease and never acquires one.

        ``reuse`` attaches to an active lease of this client for exactly the
        same selector (and exporter name) instead of acquiring a new one; it
        defaults to the session's ``reuse_lease``, which is on. A reused
        lease is not released.

        | Acquire Lease | selector=board=rpi4 | duration=1 hour |
        | Acquire Lease | selector=board=rpi4 | reuse=False |
        """
        name = self._name(session)
        if (current := sessions.opened(name)) is not None:
            if text(selector) or text(exporter_name) or text(lease_name):
                raise JumpstarterRobotError(f"session '{name}' is already connected; use Release Lease first")
            # connected from its declaration already (a lease, the shell or local mode): nothing to acquire
            with mapped_errors("Acquire Lease"):
                current.check_alive()
                robot_logger.info(f"Jumpstarter: session '{name}' is already connected to {current.exporter}")
                if self.auto_import and name in self._owned:
                    self._import_drivers(current)  # the ones an earlier, failed attempt did not import
            return current.lease_name
        with mapped_errors("Acquire Lease"):
            target = None
            if os.environ.get(JUMPSTARTER_HOST):
                opened = sessions.open_shell(name)
            else:
                base = sessions.registered_config(name)
                if base is None:
                    base = self.config if self.config.name == name else session_config(name)
                leases = text(selector) or text(exporter_name) or text(lease_name) or os.environ.get(JMP_LEASE)
                if not (leases or base.wants_lease) and base.exporter_config:
                    # local mode: the same suite setup serves the exporter config instead
                    return self._open(base.open_local(), base.local_target).lease_name
                target = target_of(
                    base,
                    selector=text(selector),
                    exporter_name=text(exporter_name),
                    lease_name=text(lease_name),
                    config=text(config),
                )
                opened = base.open_lease(
                    selector=text(selector),
                    exporter_name=text(exporter_name),
                    lease_name=text(lease_name),
                    config=text(config),
                    duration=duration_of(duration),
                    acquisition_timeout=duration_of(acquisition_timeout),
                    allow_disabled=allow_disabled or None,
                    reuse_lease=reuse,
                )
            return self._open(opened, target).lease_name

    @keyword
    def release_lease(self, session: str | None = None) -> None:
        """Disconnect a session, releasing its lease if it was acquired here.

        Attached leases (``lease_name``, ``jmp shell``) stay with their owner.
        Does nothing when the session is not connected.
        """
        context.close_session(self._name(session))

    @keyword
    def connect_to_shell_session(self, session: str | None = None) -> str:
        """Use the ``jmp shell`` this run was started from; returns the exporter name."""
        name = self._name(session)
        if not os.environ.get(JUMPSTARTER_HOST):
            raise JumpstarterRobotError("not running inside `jmp shell` (JUMPSTARTER_HOST is not set)")
        if (current := sessions.opened(name)) is not None:
            return current.exporter
        with mapped_errors("Connect To Shell Session"):
            return self._open(sessions.open_shell(name)).exporter

    @keyword
    def start_local_exporter(self, exporter_config: str, session: str | None = None) -> str:
        """Serve a local exporter config in this process and connect to it.

        ``exporter_config`` is a path or an alias from ``jmp config exporter
        list``. Returns the exporter name.
        """
        name = self._name(session)
        if sessions.opened(name) is not None:
            raise JumpstarterRobotError(f"session '{name}' is already connected; use Stop Local Exporter first")
        with mapped_errors("Start Local Exporter"):
            target = session_config(name, exporter_config=exporter_config).local_target
            return self._open(sessions.open_local(name, config=exporter_config), target).exporter

    @keyword
    def stop_local_exporter(self, session: str | None = None) -> None:
        """Stop a session's exporter and disconnect.

        A session that declares its exporter config (this library's
        ``exporter_config``, or a `Session` library) restarts when a later
        keyword needs it; one opened by `Start Local Exporter` needs that
        keyword again.
        """
        context.close_session(self._name(session))

    @keyword
    def switch_session(self, session: str) -> str:
        """Make ``session`` the one keywords use when none is given; returns the previous one.

        The switch lasts until the end of the suite that makes it, child
        suites included.
        """
        return context.switch_session(session)

    @keyword
    def get_lease_name(self, session: str | None = None) -> str | None:
        """Return the session's lease name, or None for a local exporter."""
        return self._session(session).lease_name

    @keyword
    def get_exporter_name(self, session: str | None = None) -> str:
        return self._session(session).exporter

    @keyword
    def get_exporter_labels(self, session: str | None = None) -> dict[str, str]:
        return DotDict(self._session(session).labels)

    @keyword
    def lease_should_be_active(self, session: str | None = None) -> None:
        """Fail unless the session is connected and its lease has not ended."""
        with mapped_errors("Lease Should Be Active"):
            self._session(session).check_alive()

    def _session(self, session: str | None, *, alive: bool = False) -> sessions.Session:
        """The session a keyword uses; with ``alive``, failing as `on_lease_end` says once its lease ended."""
        with mapped_errors("Jumpstarter"):
            found = context.session(text(session))
            if alive:
                found.check_alive()
            return found

    # ---- generic driver access ----

    @keyword
    def call_driver(self, path: str, method: str, *args: Any, session: str | None = None, **kwargs: Any) -> Any:
        """Call any client method by driver path, including ones without a keyword.

        Text arguments are converted to the method's annotated types, as for
        generated keywords, so ``wait=5`` reaches ``cycle(wait: int)`` as 5.

        | ${status}= | Call Driver | dutlink.power | status |
        | Call Driver | dutlink.power | cycle | wait=5 |
        """
        with sessions.lock(), mapped_errors(f"{path}.{method}"):
            node = self._session(session, alive=True).resolve(path)
            function = getattr(node, method)
            call_args, call_kwargs = convert_call(function, args, kwargs)
            with context.bounded_streams(node):
                return context.to_robot(function(*call_args, **call_kwargs), stem=method)

    @keyword
    def get_driver(self, path: str, session: str | None = None) -> DriverClient:
        """Return the Python client of a driver, for ``Call Method`` and Python keywords."""
        with mapped_errors("Get Driver"):
            return self._session(session, alive=True).resolve(path)

    @keyword
    def list_drivers(self, session: str | None = None) -> list[DotDict]:
        """List the session's drivers with their client classes, and log them as a table."""
        found = [
            DotDict(
                path=path,
                client=node.labels.get("jumpstarter.dev/client", class_path(type(node))),
                description=node.description,
            )
            for path, node in walk_tree(self._session(session, alive=True).client)
        ]
        # descriptions come from the exporter and are text, not markup
        rows = "".join(
            f"<tr><td>{escape(d.path)}</td><td>{escape(d.client)}</td><td>{escape(d.description or '')}</td></tr>"
            for d in found
        )
        robot_logger.info(f"<table><tr><th>path</th><th>client</th><th>description</th></tr>{rows}</table>", html=True)
        return found

    @keyword
    def list_driver_keywords(self, path: str = "", session: str | None = None) -> list[str]:
        """Return the keyword names of the driver at ``path``.

        Names come from the Driver library imported for that driver, so they
        carry its real prefix; a driver without one gets its full-path prefix.
        """
        with mapped_errors("List Driver Keywords"):
            target = self._session(session)
            node = target.resolve(path)
        for instance in _visible_drivers():
            if instance.default_path == path and context.session_name(instance.session_name) == target.name:
                return instance.get_keyword_names()
        return Driver(path, client=class_path(type(node)), session=text(session)).get_keyword_names()

    @keyword
    def bind_driver(self, library: str, path: str) -> str:
        """Point the Driver library imported ``AS library`` at the driver at ``path``.

        Returns the previous path. Role libraries go back to their default
        after each test.
        """
        instance = BuiltIn().get_library_instance(library)
        if not isinstance(instance, Driver):
            raise JumpstarterRobotError(f"library '{library}' is not a jumpstarter_robot.Driver")
        previous = instance.bind(path)
        try:
            with sessions.lock(), mapped_errors("Bind Driver"):
                instance.node()
        except BaseException:
            instance.bind(previous)  # a failed bind leaves the library where it was
            raise
        return previous

    @keyword
    def use_device(self, device: str, session: str | None = None) -> list[str]:
        """Bind every role library to the matching driver under ``device``.

        With role libraries ``power`` and ``console``, ``Use Device    dut2``
        binds them to ``dut2.power`` and ``dut2.console``. Returns the paths
        bound.
        """
        target = self._session(session, alive=True)
        matched = []
        for instance in _visible_drivers():
            if not instance.role or (instance.session_name or context.session_name()) != target.name:
                continue
            path = f"{device}.{instance.role}"
            try:
                target.resolve(path)
            except KeyError:
                continue
            matched.append((instance, instance.bind(path)))
        if not matched:
            raise JumpstarterRobotError(f"no role library matches a driver under '{device}'")
        try:
            with sessions.lock(), mapped_errors("Use Device"):
                for instance, _ in matched:
                    instance.node()  # checks the client class, as Bind Driver does
        except BaseException:
            for instance, previous in matched:
                instance.bind(previous)
            raise
        return [instance.path for instance, _ in matched]

    # ---- listener ----

    def start_suite(self, data, result) -> None:
        """Import the drivers of connected sessions into each suite that imports this library.

        Robot Framework imports libraries per suite, so drivers imported into
        one suite (or into ``__init__.robot`` by `Acquire Lease`) are not
        visible in the next one until they are imported there too.
        """
        self._declare()  # this instance serves later suites too; see Session.start_suite
        if not self.auto_import:
            return
        for name in sorted(self._owned):
            opened = sessions.opened(name)
            # __init__ connects for the drivers' keywords; a reused instance does it here
            reconnect = self._declares and not self._connecting and name == self.config.name and in_run()
            if opened is None and (reconnect or self._attaches_env_lease(name)):
                with mapped_errors("Jumpstarter"):
                    try:
                        opened = sessions.get(name)
                    except sessions.NoSession as e:
                        log = robot_logger.info if reconnect else robot_logger.warn
                        log(f"Jumpstarter: {e}")
            if opened is not None:
                with mapped_errors("Jumpstarter"):
                    self._import_drivers(opened)
        self._connecting = False

    def _attaches_env_lease(self, name: str) -> bool:
        """Whether to attach to ``JMP_LEASE`` now, for the driver keywords of a suite that declares no session."""
        return (
            self._attach_env_lease
            and name == self.config.name
            and in_run()
            and sessions.registered_config(name) is None
            and not context.suite_session_names()
        )

    def end_test(self, data, result) -> None:
        context.end_test()
        for instance in instances():
            if instance.role:
                instance.reset_binding()

    def end_suite(self, data, result) -> None:
        context.end_suite(data.id)

    def close(self) -> None:
        context.end_run()
        sessions.unregister_config(self.config.name, self.config)
        for name in self._owned:
            sessions.unregister_factory(name, self.config.open)
        sessions.close_each(sorted(self._owned))
