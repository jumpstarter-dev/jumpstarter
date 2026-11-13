"""Client generation settings from ``robot.toml`` (or ``pyproject.toml``).

Settings live under ``[tool.jumpstarter.client]``: generator defaults sit in
the table itself and each named sub-table is one client to generate, whose
name is also its session name::

    [tool.jumpstarter.client]
    roles = true
    config = "lab"                      # default client config (alias, path or "env")

    [tool.jumpstarter.client.dut]
    exporter-config = "exporter.yaml"   # or selector / lease / exporter-class / tree
    resource = "client.resource"

    [tool.jumpstarter.client.dut.aliases]
    "dutlink.power" = "Main Power"

    [tool.jumpstarter.client.dut.nodes]
    "dutlink.power" = "jumpstarter_driver_power.client.PowerClient"

Paths are relative to the TOML file. Reads use ``tomllib``; the ``nodes``
table is written back with ``tomlkit`` so comments and ordering survive.
Credentials never go into these files.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.items
import yaml

from .configs import ENV_CONFIG, is_path_like
from .tree import pascal_case

PROJECT_FILES = ("robot.toml", "pyproject.toml")
TABLE = ("tool", "jumpstarter", "client")

# keys of [tool.jumpstarter.client] itself: defaults for every client
DEFAULT_KEYS = ("roles", "include", "exclude", "config")
# keys of [tool.jumpstarter.client.<name>]
CLIENT_KEYS = (
    "exporter-config",
    "selector",
    "lease",
    "exporter-class",
    "driver-interface-dir",
    "tree",
    "config",
    "resource",
    "aliases",
    "nodes",
    "include",
    "exclude",
    "roles",
)
RESERVED_NAMES = frozenset((*DEFAULT_KEYS, *CLIENT_KEYS))
SECRET_KEYS = ("token", "password", "secret")


class ProjectError(ValueError):
    """The project file's client settings are invalid."""


@dataclass
class ClientSettings:
    """One client to generate, with paths resolved against the project file."""

    name: str
    base_dir: Path
    exporter_config: str | Path | None = None
    selector: str | None = None
    lease: str | None = None
    exporter_class: str | None = None
    driver_interface_dirs: list[Path] = field(default_factory=list)
    tree: Path | None = None
    config: str | Path | None = None
    resource: Path | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, Any] | None = None
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    roles: bool = False
    project: Path | None = None
    session_name: str | None = None

    @property
    def session(self) -> str:
        """The session the resource declares: ``generate --session``, else the client name."""
        return self.session_name or self.name

    @property
    def device_name(self) -> str:
        return pascal_case(self.name) or "Device"

    @property
    def resource_path(self) -> Path:
        return self.resource or self.base_dir / f"client-{self.name}.resource"

    def source_args(self) -> dict[str, Any]:
        """The session variable defaults the generated resource carries."""
        return {
            "exporter_config": self.exporter_config,
            "selector": self.selector,
            "lease_name": self.lease,
            "config": self.config,
        }

    def nodes_ref(self) -> str:
        where = self.project.name if self.project else "the command line"
        return f"{where} [{'.'.join(TABLE)}.{self.name}.nodes]"


# ---- finding and reading ----


def _client_table(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    table: Any = data
    for key in TABLE:
        if not isinstance(table, Mapping) or key not in table:
            return None
        table = table[key]
    return table if isinstance(table, Mapping) else None


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path}: not valid TOML: {e}") from None


def find_project(start: str | Path | None = None) -> Path | None:
    """The nearest ``robot.toml`` (or ``pyproject.toml`` with ``[tool.jumpstarter.client]``) upwards from ``start``.

    In each directory a ``robot.toml`` with client settings wins, then a
    ``pyproject.toml`` with them, then a bare ``robot.toml`` (the file
    ``--capture`` writes to).
    """
    directory = Path(start or Path.cwd()).resolve()
    for folder in (directory, *directory.parents):
        robot = folder / "robot.toml"
        pyproject = folder / "pyproject.toml"
        if robot.is_file() and _client_table(_read(robot)) is not None:
            return robot
        if pyproject.is_file() and _client_table(_read(pyproject)) is not None:
            return pyproject
        if robot.is_file():
            return robot
    return None


def _strings(where: str, value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ProjectError(f"{where} must be a string or a list of strings")


def _string(where: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectError(f"{where} must be a string")
    return value


def _check_keys(where: str, table: Mapping[str, Any], allowed: Iterable[str]) -> None:
    allowed = tuple(allowed)
    for key in table:
        if any(secret in key.lower() for secret in SECRET_KEYS):
            raise ProjectError(
                f"{where}.{key}: credentials do not belong in the repository; "
                "use a client config alias, or a committed client config YAML without a token plus JMP_TOKEN"
            )
        if key not in allowed:
            raise ProjectError(f"{where}: unknown key '{key}' (known: {', '.join(allowed)})")


def config_value(value: str, base_dir: Path) -> str | Path:
    """A ``config`` or ``exporter-config`` value: aliases (and ``env``) stay strings, paths resolve."""
    if value == ENV_CONFIG or not is_path_like(value):
        return value
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def _path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def _defaults(where: str, table: Mapping[str, Any]) -> dict[str, Any]:
    scalars = {k: v for k, v in table.items() if not isinstance(v, Mapping)}
    _check_keys(where, scalars, DEFAULT_KEYS)
    return scalars


def _aliases(where: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(isinstance(v, str) for v in value.values()):
        raise ProjectError(f"{where} must be a table of tree path = \"Keyword Prefix\"")
    return {str(k): v for k, v in value.items()}


def _nodes(where: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectError(f"{where} must be a table of tree path = client class")
    return dict(value)


def _bool(where: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ProjectError(f"{where} must be true or false")
    return value


def _exporter_class(where: str, value: Any, base: Path) -> str:
    path, sep, name = _string(where, value).partition("#")
    return str(_path(path, base)) + (f"#{name}" if sep else "")


# key → (attribute, parse(where, value, base_dir))
_PARSERS: dict[str, tuple[str, Any]] = {
    "exporter-config": ("exporter_config", lambda w, v, b: config_value(_string(w, v), b)),
    "config": ("config", lambda w, v, b: config_value(_string(w, v), b)),
    "selector": ("selector", lambda w, v, b: _string(w, v)),
    "lease": ("lease", lambda w, v, b: _string(w, v)),
    "exporter-class": ("exporter_class", _exporter_class),
    "driver-interface-dir": ("driver_interface_dirs", lambda w, v, b: [_path(p, b) for p in _strings(w, v)]),
    "tree": ("tree", lambda w, v, b: _path(_string(w, v), b)),
    "resource": ("resource", lambda w, v, b: _path(_string(w, v), b)),
    "aliases": ("aliases", lambda w, v, b: _aliases(w, v)),
    "nodes": ("nodes", lambda w, v, b: _nodes(w, v)),
    "include": ("include", lambda w, v, b: _strings(w, v)),
    "exclude": ("exclude", lambda w, v, b: _strings(w, v)),
    "roles": ("roles", lambda w, v, b: _bool(w, v)),
}


def _apply(settings: ClientSettings, key: str, value: Any, where: str) -> None:
    attribute, parse = _PARSERS[key]
    setattr(settings, attribute, parse(where, value, settings.base_dir))


@dataclass
class Project:
    """The client settings of one project file."""

    path: Path
    defaults: dict[str, Any]
    clients: dict[str, ClientSettings]

    @classmethod
    def load(cls, path: str | Path) -> Project:
        path = Path(path).resolve()
        if not path.is_file():
            raise ProjectError(f"project file {path} does not exist")
        table = _client_table(_read(path)) or {}
        where = ".".join(TABLE)
        defaults = _defaults(where, table)
        clients = {}
        for name, value in table.items():
            if not isinstance(value, Mapping):
                continue
            if name in RESERVED_NAMES:
                raise ProjectError(f"[{where}.{name}]: '{name}' is a setting name and cannot name a client")
            clients[name] = client_settings(name, value, defaults, base_dir=path.parent, project=path)
        return cls(path=path, defaults=defaults, clients=clients)

    def client(self, name: str) -> ClientSettings:
        try:
            return self.clients[name]
        except KeyError:
            known = ", ".join(self.clients) or "none"
            raise ProjectError(f"{self.path.name} has no client '{name}' (clients: {known})") from None


def client_settings(
    name: str,
    table: Mapping[str, Any],
    defaults: Mapping[str, Any] | None = None,
    *,
    base_dir: Path,
    project: Path | None = None,
) -> ClientSettings:
    """Settings of one client: its table over the generator defaults."""
    where = f"{'.'.join(TABLE)}.{name}"
    _check_keys(where, table, CLIENT_KEYS)
    settings = ClientSettings(name=name, base_dir=base_dir, project=project)
    for key, value in {**(defaults or {}), **table}.items():
        _apply(settings, key, value, f"{where}.{key}")
    return settings


def token_warnings(settings: ClientSettings) -> list[str]:
    """Warn when a committed client config YAML (``config = "path.yaml"``) carries a token."""
    path = settings.config
    if not isinstance(path, Path) or not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []
    if isinstance(data, Mapping) and data.get("token"):
        message = (
            f"client '{settings.name}': {path} contains a token; do not commit it. Commit the endpoint and TLS "
            "settings only and pass the token in JMP_TOKEN (or use config = \"env\")"
        )
        return [message]
    return []


# ---- writing the nodes table ----


def _table(parent: Any, key: str, *, super_table: bool) -> Any:
    if key not in parent:
        parent[key] = tomlkit.table(is_super_table=super_table)
    child = parent[key]
    if not isinstance(child, (dict, tomlkit.items.Table)):
        raise ProjectError(f"'{key}' in the project file is not a table")
    return child


def _toml_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        inline = tomlkit.inline_table()
        inline.update(dict(value))
        return inline
    return value


def _refuse_secrets(mapping: Mapping[str, Any], where: str) -> None:
    for key, value in mapping.items():
        if any(secret in str(key).lower() for secret in SECRET_KEYS):
            raise ProjectError(f"refusing to write '{key}' to {where}: credentials stay out of the repository")
        if isinstance(value, Mapping):
            _refuse_secrets(value, where)


def _update_nodes(nodes: Any, mapping: Mapping[str, Any]) -> None:
    """Make ``nodes`` equal ``mapping`` in place, so unchanged entries keep their comments."""
    for key in [k for k in nodes if k not in mapping]:
        del nodes[key]
    for key, value in mapping.items():
        current = nodes.get(key)
        if current is not None and _plain(current) == _plain(value):
            continue
        nodes[key] = _toml_value(value)


def _plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def write_nodes(
    path: str | Path,
    name: str,
    mapping: Mapping[str, Any],
    *,
    settings: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``[tool.jumpstarter.client.<name>.nodes]`` into ``path``, creating the file or table if needed.

    ``settings`` are written as the client's keys when the client table is
    new (``selector``, ``resource``, ...). Everything else in the file is
    left as it was.
    """
    path = Path(path)
    if name in RESERVED_NAMES:
        raise ProjectError(f"'{name}' is a setting name and cannot name a client")
    _refuse_secrets(mapping, str(path))
    _refuse_secrets(settings or {}, str(path))
    document = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    table = document
    for key in TABLE:
        table = _table(table, key, super_table=True)
    is_new = name not in table
    client = _table(table, name, super_table=False)
    if is_new:
        for key, value in (settings or {}).items():
            if value is not None:
                client[key] = value
    nodes = client.get("nodes")
    if nodes is None or any(isinstance(_plain(v), Mapping) and "client" not in _plain(v) for v in nodes.values()):
        client["nodes"] = tomlkit.table()  # new, or a nested layout that flat keys cannot extend
        nodes = client["nodes"]
    _update_nodes(nodes, mapping)
    path.write_text(tomlkit.dumps(document))
    return path
