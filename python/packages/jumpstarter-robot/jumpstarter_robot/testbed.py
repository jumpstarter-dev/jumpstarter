"""Testbeds: one variable file that says where every session connects.

A testbed is a Robot Framework variable file (YAML, or Python) given with
``robot -V testbeds/lab-eu.yaml``. Next to the suite's own test data, a
``JUMPSTARTER`` mapping configures the sessions of the run::

    JUMPSTARTER:
      config: lab-eu              # for every session
      sessions:
        rpi4:
          selector: board=rpi4,lab=eu
        android:
          lease_name: pixel-7-shared

Keys use the ``Session`` library's argument names, with ``-`` or ``_``.
A testbed overrides the generated resource's defaults and the import's
arguments; a session variable given on the command line or in a variable file
(``JMP_RPI4_SELECTOR``) overrides the testbed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .errors import JumpstarterRobotError
from .naming import session_variable

if TYPE_CHECKING:
    from .session_library import SessionConfig

TESTBED_VARIABLE = "JUMPSTARTER"

# SessionConfig attribute → the generated resource's variable for it
RESOURCE_VARIABLES = {
    "exporter_config": "JMP_EXPORTER_CONFIG",
    "selector": "JMP_SELECTOR",
    "lease_name": "JMP_LEASE_NAME",
    "config": "JMP_CLIENT_CONFIG",
}
SETTINGS = (
    *RESOURCE_VARIABLES,
    "exporter_name",
    "duration",
    "acquisition_timeout",
    "allow_disabled",
    "release_grace",
    "reuse_lease",
    "on_lease_end",
)
BOOLEANS = ("allow_disabled", "reuse_lease")


def _keys(table: Mapping[str, Any], where: str) -> dict[str, Any]:
    settings = {}
    for key, value in table.items():
        name = str(key).replace("-", "_")
        if name not in SETTINGS:
            raise JumpstarterRobotError(
                f"testbed {where}: unknown setting '{key}'; expected one of {', '.join(SETTINGS)}"
            )
        settings[name] = value
    return settings


def settings_for(testbed: Any, session: str) -> dict[str, Any]:
    """The settings ``testbed`` gives ``session``: its shared keys, then the session's own."""
    if testbed is None:
        return {}
    if not isinstance(testbed, Mapping):
        raise JumpstarterRobotError(f"${{{TESTBED_VARIABLE}}} must be a mapping, not {type(testbed).__name__}")
    shared = {key: value for key, value in testbed.items() if key != "sessions"}
    sessions = testbed.get("sessions") or {}
    if not isinstance(sessions, Mapping):
        raise JumpstarterRobotError(f"testbed 'sessions' must map session names to settings, not {sessions!r}")
    own = sessions.get(session) or {}
    if not isinstance(own, Mapping):
        raise JumpstarterRobotError(f"testbed session '{session}' must be a mapping of settings, not {own!r}")
    return {**_keys(shared, "shared settings"), **_keys(own, f"session '{session}'")}


def _boolean(value: Any) -> bool:
    from robot.utils import is_truthy

    return value if isinstance(value, bool) else is_truthy(value)


def apply(config: SessionConfig, settings: Mapping[str, Any], given: Mapping[str, str | None]) -> str | None:
    """Apply a testbed's ``settings`` to ``config``; returns its ``on_lease_end``, if any.

    ``given`` maps resource variables to their command-line values; a setting
    whose session variable was given there is left alone.
    """
    from robot.utils import timestr_to_secs

    from .session_library import duration_of, text

    for name, value in settings.items():
        variable = RESOURCE_VARIABLES.get(name)
        if variable is not None and given.get(session_variable(variable, config.name)) is not None:
            continue
        if name in ("duration", "acquisition_timeout"):
            setattr(config, name, duration_of(value) or (config.duration if name == "duration" else None))
        elif name == "release_grace":
            config.release_grace = timestr_to_secs(value)
        elif name in BOOLEANS:
            setattr(config, name, _boolean(value))
        elif name != "on_lease_end":
            setattr(config, name, text(value))
    return text(settings.get("on_lease_end"))
