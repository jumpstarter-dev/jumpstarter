"""``Session``: declare how a named session connects.

Robot Framework ignores a second import of the same library with different
arguments, so one ``Jumpstarter`` import cannot describe two exporters. Each
generated client resource therefore declares its session with this small
library instead, and imports ``Jumpstarter`` without arguments::

    Library    jumpstarter_robot.Jumpstarter
    Library    jumpstarter_robot.Session    android    exporter_config=${CURDIR}/exporter-android.yaml
    ...    AS    session.android
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import context, sessions
from .errors import JumpstarterRobotError, mapped_errors
from .naming import title
from jumpstarter.config.env import JMP_LEASE, JUMPSTARTER_HOST


def text(value: Any) -> str | None:
    """Treat empty strings (``${EMPTY}`` from a generated resource) as unset."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def duration_of(value: Any) -> timedelta | None:
    from robot.utils import timestr_to_secs

    value = text(value)
    return None if value is None else timedelta(seconds=timestr_to_secs(value))


def in_run() -> bool:
    """Whether Robot Framework is executing tests; a dry run (``--dryrun``) is not, and must not connect."""
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    return current is not None and not current.dry_run


def dry_run() -> bool:
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    return current is not None and current.dry_run


# Generic command-line variables a named session falls back to.
COMMAND_LINE_FALLBACKS = (
    ("selector", "JMP_SELECTOR"),
    ("lease_name", "JMP_LEASE_NAME"),
    ("config", "JMP_CLIENT_CONFIG"),
)


def global_value(name: str) -> Any:
    """A variable of the run's global scope: ``robot -v``, a ``-V`` variable file, or ``Set Global Variable``.

    Never one a resource defines, so another session's resource cannot lend
    it its value.
    """
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    if current is None:
        return None
    try:
        values = current.variables._global.as_dict()
    except AttributeError:
        return None
    # a variable file's mapping is stored as &{NAME}, a list as @{NAME}
    for decorated in (f"${{{name}}}", f"&{{{name}}}", f"@{{{name}}}", name):
        if decorated in values:
            return values[decorated]
    return None


def command_line_value(name: str) -> str | None:
    """A global variable as text (``robot -v NAME:value`` or a variable file), or None.

    A named session's resource has its own variables; reading the generic one
    from the global scope only lets ``-v JMP_SELECTOR:...`` reach it without a
    default-session resource in the same suite lending it its own value.
    """
    return text(global_value(name))


def testbed_names(session: str) -> bool:
    """Whether the run's testbed has an entry under ``sessions`` for ``session``."""
    from . import testbed

    table = global_value(testbed.TESTBED_VARIABLE) if in_run() else None
    sessions_table = table.get("sessions") if isinstance(table, Mapping) else None
    return isinstance(sessions_table, Mapping) and session in sessions_table


def apply_run_settings(config: SessionConfig) -> str | None:
    """Apply the run's testbed and command-line fallbacks to ``config``; returns the testbed's ``on_lease_end``.

    Precedence, highest first: the session's own variables given with ``-v``
    or a variable file (already in ``config``), the testbed's
    ``${JUMPSTARTER}`` settings, the resource's or import's values, and for
    a named session the generic ``-v JMP_SELECTOR``, ``JMP_LEASE_NAME`` and
    ``JMP_CLIENT_CONFIG``.
    """
    from . import testbed
    from .naming import session_variable

    if not in_run():
        return None
    given = {
        session_variable(variable, config.name): command_line_value(session_variable(variable, config.name))
        for variable in testbed.RESOURCE_VARIABLES.values()
    }
    settings = testbed.settings_for(global_value(testbed.TESTBED_VARIABLE), config.name)
    on_lease_end = testbed.apply(config, settings, given)
    if config.name != sessions.DEFAULT_SESSION:
        for attribute, variable in COMMAND_LINE_FALLBACKS:
            if getattr(config, attribute) is None:
                setattr(config, attribute, command_line_value(variable))
    return on_lease_end


@dataclass
class SessionConfig:
    """How to open one named session; the first source that applies wins.

    Inside ``jmp shell`` the shell is always used; then an existing lease:
    the lease name (or ``JMP_LEASE``), else the active lease of this client
    that matches the selector or exporter name; then a local exporter config.
    Opening a session never acquires a lease: ``Acquire Lease`` does.
    """

    name: str = sessions.DEFAULT_SESSION
    exporter_config: str | None = None
    selector: str | None = None
    exporter_name: str | None = None
    lease_name: str | None = None
    config: str | None = None
    duration: timedelta = timedelta(minutes=30)
    acquisition_timeout: timedelta | None = None
    allow_disabled: bool = False
    release_grace: float = 1.0
    reuse_lease: bool = True
    config_dir: Path | None = None

    @property
    def wants_lease(self) -> bool:
        return bool(self.selector or self.exporter_name or self.lease_name)

    @property
    def configured(self) -> bool:
        return bool(
            os.environ.get(JUMPSTARTER_HOST) or self.wants_lease or os.environ.get(JMP_LEASE) or self.exporter_config
        )

    def describe(self) -> str:
        if os.environ.get(JUMPSTARTER_HOST):
            return "the jmp shell session"
        if self.wants_lease or os.environ.get(JMP_LEASE):
            return f"a lease ({self.selector or self.exporter_name or self.lease_name or os.environ.get(JMP_LEASE)})"
        return f"the local exporter {self.exporter_config}"

    def open(self) -> sessions.Session:
        """Connect when a keyword first needs the session; this attaches to a lease, never acquires one."""
        if os.environ.get(JUMPSTARTER_HOST):
            return sessions.open_shell(self.name)
        if self.wants_lease or os.environ.get(JMP_LEASE):
            return self.open_lease(acquire=False)
        if self.exporter_config:
            return self.open_local()
        raise sessions.NoSession(f"session '{self.name}' has nothing to connect to")

    def open_local(self) -> sessions.Session:
        return sessions.open_local(self.name, config=self.exporter_config, config_dir=self.config_dir)

    def open_lease(self, **overrides: Any) -> sessions.Session:
        values = {
            "config": self.config,
            "config_dir": self.config_dir,
            "selector": self.selector,
            "exporter_name": self.exporter_name,
            "lease_name": self.lease_name,
            "duration": self.duration,
            "acquisition_timeout": self.acquisition_timeout,
            "allow_disabled": self.allow_disabled,
            "release_grace": self.release_grace,
            "reuse_lease": self.reuse_lease,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        if in_run():
            from robot.api import logger

            target = values["selector"] or values["exporter_name"] or values["lease_name"] or os.environ.get(JMP_LEASE)
            verb = "leasing" if values.get("acquire", True) else "attaching to a lease for"
            logger.console(f"Jumpstarter: {verb} {target} for session '{self.name}' ...")
        return sessions.open_lease(self.name, **values)


def session_config(
    name: Any,
    *,
    exporter_config: Any = None,
    selector: Any = None,
    exporter_name: Any = None,
    lease_name: Any = None,
    config: Any = None,
    duration: Any = "30 minutes",
    acquisition_timeout: Any = None,
    allow_disabled: bool = False,
    release_grace: Any = "1 s",
    exporter_class: Any = None,
    reuse_lease: bool = True,
) -> SessionConfig:
    from robot.utils import timestr_to_secs

    if text(exporter_class):
        raise JumpstarterRobotError(
            "exporter_class: leasing by ExporterClass is not supported by this Jumpstarter client yet"
        )
    return SessionConfig(
        name=text(name) or sessions.DEFAULT_SESSION,
        exporter_config=text(exporter_config),
        selector=text(selector),
        exporter_name=text(exporter_name),
        lease_name=text(lease_name),
        config=text(config),
        duration=duration_of(duration) or timedelta(minutes=30),
        acquisition_timeout=duration_of(acquisition_timeout),
        allow_disabled=allow_disabled,
        release_grace=timestr_to_secs(release_grace),
        reuse_lease=reuse_lease,
    )


class Session:
    """Declares one named Jumpstarter session.

    The session opens the first time a keyword needs it, from the first of:
    the ``jmp shell`` the run is inside, an existing lease (``lease_name``
    or ``JMP_LEASE``, else the active lease matching ``selector`` or
    ``exporter_name``, using the client config ``config``), or the local
    ``exporter_config``. It never acquires a lease: `Acquire Lease` does.
    ``config`` is a client
    config alias, a client config YAML path or ``env``; ``exporter_config``
    is an exporter config path or alias.

    Keywords that take no ``session`` argument use the session declared in
    the suite when there is exactly one, so a suite that imports one client
    resource never needs to name its session.

    A lease `Acquire Lease` acquired is released at the end of the run;
    attached leases never are. ``reuse_lease=False`` stops the session from
    attaching to a matching lease, as a pabot pool needs.

    ``on_lease_end`` is ``fatal`` (stop the run) or ``fail`` (fail the
    keyword) when this session's lease ends; it defaults to the run's policy,
    set with the ``Jumpstarter`` library's ``on_lease_end``.
    """

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(
        self,
        name: str = sessions.DEFAULT_SESSION,
        exporter_config: str | None = None,
        selector: str | None = None,
        exporter_name: str | None = None,
        lease_name: str | None = None,
        config: str | None = None,
        duration: str = "30 minutes",
        acquisition_timeout: str | None = None,
        allow_disabled: bool = False,
        release_grace: str = "1 s",
        exporter_class: str | None = None,
        reuse_lease: bool = True,
        on_lease_end: str | None = None,
    ):
        self.ROBOT_LIBRARY_LISTENER = self
        self.config = session_config(
            name,
            exporter_config=exporter_config,
            selector=selector,
            exporter_name=exporter_name,
            lease_name=lease_name,
            config=config,
            duration=duration,
            acquisition_timeout=acquisition_timeout,
            allow_disabled=allow_disabled,
            release_grace=release_grace,
            exporter_class=exporter_class,
            reuse_lease=reuse_lease,
        )
        self.name = self.config.name
        # the testbed, like every other setting it gives, wins over the import's
        on_lease_end = apply_run_settings(self.config) or text(on_lease_end)
        if on_lease_end:
            sessions.set_lease_end_policy(str(on_lease_end).strip(), session=self.name)
        setattr(self, context.SESSION_MARKER, self.name)
        self._factory = self.config.open
        sessions.register_factory(self.name, self._factory)
        sessions.register_config(self.name, self.config)
        words = title(self.name)
        self._keywords = {
            f"Connect {words} Session": self._connect,
            f"Release {words} Session": self._release,
        }

    def get_keyword_names(self) -> list[str]:
        return list(self._keywords)

    def get_keyword_arguments(self, name: str) -> list[str]:
        # without this Robot Framework assumes *varargs and **kwargs and ignores extra arguments
        return []

    def get_keyword_documentation(self, name: str) -> str:
        if name.startswith("Connect"):
            return (
                f"Open the ``{self.name}`` session now and return its exporter name; it otherwise opens on first use."
            )
        if name.startswith("Release"):
            return (
                f"Close the ``{self.name}`` session, releasing a lease it acquired. "
                "It reopens if a later keyword needs it."
            )
        return Session.__doc__ or ""

    def run_keyword(self, name: str, args: list[Any], named: dict[str, Any] | None = None) -> Any:
        return self._keywords[name]()

    def _connect(self) -> str:
        with sessions.lock(), mapped_errors(f"Connect {title(self.name)} Session"):
            return sessions.get(self.name).exporter

    def _release(self) -> None:
        context.close_session(self.name)

    def end_test(self, data, result) -> None:
        context.end_test()

    def end_suite(self, data, result) -> None:
        context.end_suite(data.id)

    def close(self) -> None:
        context.end_run()
        sessions.unregister_factory(self.name, self._factory)
        sessions.unregister_config(self.name, self.config)
        sessions.close(self.name)
