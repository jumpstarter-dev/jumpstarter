"""Client-only overlay built from an exporter config's export section.

The overlay instantiates driver *clients* (including nested children and refs)
and never starts the corresponding drivers. Server-side calls on those clients
fail. A ref may name another overlay driver or a driver already on the session.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from dataclasses import fields
from pathlib import Path
from typing import Any, Protocol

import yaml
from anyio.from_thread import BlockingPortal
from pydantic import ValidationError

from jumpstarter.client.base import DriverClient
from jumpstarter.client.core import DriverError
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.importlib import import_class
from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1,
    ExporterConfigV1Alpha1DriverInstance,
    ExporterConfigV1Alpha1DriverInstanceBase,
    ExporterConfigV1Alpha1DriverInstanceComposite,
    ExporterConfigV1Alpha1DriverInstanceProxy,
)

# Fields the overlay sets itself. Matching keys inside ``config:`` are server
# constructor arguments, or would clobber the client tree, so they are dropped.
_MANAGED_FIELDS = frozenset({
    "stub",
    "portal",
    "stack",
    "children",
    "uuid",
    "labels",
    "logger",
    "description",
    "methods_description",
})

_COMPOSITE_CLIENT = "jumpstarter_driver_composite.client.CompositeClient"


class _HasChildren(Protocol):
    children: dict[str, DriverClient]


class _OverlayTree:
    """Temporary parent for newly built overlay clients.

    This is not a driver client. Dropping it must not close the children, which
    are either installed on the session or still referenced by the caller.
    """

    def __init__(self) -> None:
        self.children: dict[str, DriverClient] = {}


class LocalOnlyStub:
    """Rejects server-side RPCs for a client-only overlay driver."""

    def __init__(self, name: str):
        self.name = name

    def _fail(self, method: str) -> None:
        raise DriverError(
            f"Server-side call {method!r} is not supported for client-only driver {self.name!r}"
        )

    async def DriverCall(self, request):
        self._fail(getattr(request, "method", "DriverCall"))

    def StreamingDriverCall(self, request):
        self._fail(getattr(request, "method", "StreamingDriverCall"))

    def Stream(self, *args, **kwargs):
        self._fail("stream")

    async def GetStatus(self, request):
        self._fail("GetStatus")

    def LogStream(self, request):
        self._fail("LogStream")

    async def EndSession(self, request):
        self._fail("EndSession")

    async def GetReport(self, request):
        self._fail("GetReport")


def prepare_client_overlay(path: str) -> str:
    """Load an exporter config and validate its export section.

    Returns the absolute path. Endpoint, token, tls, and hooks are ignored.
    A ref to a driver that is not in the file is accepted here; it is resolved
    against the session client when the overlay is applied.
    """
    resolved, config = _load_overlay_config(path)
    validate_client_overlay(config.export)
    return str(resolved)


def validate_client_overlay(export: dict[str, ExporterConfigV1Alpha1DriverInstance]) -> None:
    """Fail if the export section cannot be built as a client-only tree.

    Refs that leave the export are not an error: those names are resolved on
    the session client at apply time. Cycles and malformed refs still fail.
    """
    if not export:
        raise ConfigurationError("Client overlay export section is empty")
    concrete, refs = _index_export(export)
    for ref in refs.values():
        _follow_ref(ref, concrete, refs)


def build_client_overlay(
    export: dict[str, ExporterConfigV1Alpha1DriverInstance],
    portal: BlockingPortal,
    stack: ExitStack,
    session: _HasChildren | None = None,
) -> _OverlayTree:
    """Build overlay clients for the export section.

    ``session`` supplies drivers a ref may name that are not declared in the
    export. Without it, such a ref is an error.
    """
    validate_client_overlay(export)
    root = _OverlayTree()
    registry: dict[str, DriverClient] = {}
    owners: dict[str, _HasChildren] = {"": root}
    pending: list[tuple[str, str, str]] = []
    ref_targets: dict[str, str] = {}
    for name, instance in export.items():
        _add_node(
            root.children,
            "",
            name,
            instance,
            name,
            portal,
            stack,
            registry,
            owners,
            pending,
            ref_targets,
        )
    concrete = set(registry)
    # Resolve every ref before linking any of them, so a failure cannot leave a
    # session client parented under a tree that is about to be discarded.
    resolved: list[tuple[_HasChildren, str, DriverClient]] = []
    for parent_path, name, ref in pending:
        resolved.append((
            owners[parent_path],
            name,
            _client_for_ref(ref, concrete, ref_targets, registry, session),
        ))
    for parent, name, target in resolved:
        parent.children[name] = target
    return root


def apply_client_overlay(
    client: _HasChildren,
    export: dict[str, ExporterConfigV1Alpha1DriverInstance],
    portal: BlockingPortal,
    stack: ExitStack,
) -> None:
    """Merge export clients onto ``client``. Existing names are replaced."""
    overlay = build_client_overlay(export, portal, stack, session=client)
    transferred = dict(overlay.children)
    # The temporary tree must not outlive this function while still parenting
    # the clients. A composite client destructor closes its children.
    overlay.children.clear()
    for name, child in transferred.items():
        if name in client.children:
            print(
                f"Warning: client overlay replaced existing client '{name}'",
                file=sys.stderr,
            )
        client.children[name] = child


def apply_client_overlay_from_path(
    client: _HasChildren,
    path: str,
    portal: BlockingPortal,
    stack: ExitStack,
) -> None:
    """Load ``path`` and merge its export section onto ``client``."""
    _, config = _load_overlay_config(path)
    apply_client_overlay(client, config.export, portal, stack)


def _load_overlay_config(path: str) -> tuple[Path, ExporterConfigV1Alpha1]:
    resolved = Path(path).expanduser().resolve()
    try:
        config = ExporterConfigV1Alpha1.load_path(resolved)
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Failed to load client overlay '{resolved}': {exc}") from exc
    return resolved, config


def _composite_client_class() -> type[DriverClient]:
    """Import the composite client only when a composite node is built."""
    try:
        from jumpstarter_driver_composite.client import CompositeClient
    except ModuleNotFoundError as exc:
        raise ConfigurationError(
            "Client overlay composite nodes require jumpstarter-driver-composite. "
            "Install it with: pip install jumpstarter-driver-composite"
        ) from exc
    return CompositeClient


def _index_export(
    export: dict[str, ExporterConfigV1Alpha1DriverInstance],
) -> tuple[set[str], dict[str, str]]:
    concrete: set[str] = set()
    refs: dict[str, str] = {}

    def walk(instances: dict[str, ExporterConfigV1Alpha1DriverInstance], prefix: str) -> None:
        for name, instance in instances.items():
            path = f"{prefix}.{name}" if prefix else name
            match instance.root:
                case ExporterConfigV1Alpha1DriverInstanceProxy(ref=ref):
                    _check_ref(ref)
                    refs[path] = ref
                case ExporterConfigV1Alpha1DriverInstanceComposite():
                    concrete.add(path)
                    walk(instance.root.children, path)
                case ExporterConfigV1Alpha1DriverInstanceBase(type=driver_type):
                    _import_client_class(driver_type)
                    concrete.add(path)
                    walk(instance.root.children, path)
                case _:
                    raise ConfigurationError(f"Unknown driver instance type: {type(instance.root)}")

    walk(export, "")
    return concrete, refs


def _add_node(
    parent_children: dict[str, DriverClient],
    parent_path: str,
    name: str,
    instance: ExporterConfigV1Alpha1DriverInstance,
    path: str,
    portal: BlockingPortal,
    stack: ExitStack,
    registry: dict[str, DriverClient],
    owners: dict[str, _HasChildren],
    pending: list[tuple[str, str, str]],
    ref_targets: dict[str, str],
) -> None:
    match instance.root:
        case ExporterConfigV1Alpha1DriverInstanceProxy(ref=ref):
            _check_ref(ref)
            ref_targets[path] = ref
            pending.append((parent_path, name, ref))
        case ExporterConfigV1Alpha1DriverInstanceComposite():
            client = _make_client(
                _composite_client_class(),
                _COMPOSITE_CLIENT,
                name,
                path,
                None,
                {},
                {},
                portal,
                stack,
            )
            registry[path] = client
            owners[path] = client
            parent_children[name] = client
            for child_name, child in instance.root.children.items():
                _add_node(
                    client.children,
                    path,
                    child_name,
                    child,
                    f"{path}.{child_name}",
                    portal,
                    stack,
                    registry,
                    owners,
                    pending,
                    ref_targets,
                )
        case ExporterConfigV1Alpha1DriverInstanceBase() as root:
            client_class, client_path = _import_client_class(root.type)
            client = _make_client(
                client_class,
                client_path,
                name,
                path,
                root.description,
                root.methods_description,
                root.config,
                portal,
                stack,
            )
            registry[path] = client
            owners[path] = client
            parent_children[name] = client
            for child_name, child in root.children.items():
                _add_node(
                    client.children,
                    path,
                    child_name,
                    child,
                    f"{path}.{child_name}",
                    portal,
                    stack,
                    registry,
                    owners,
                    pending,
                    ref_targets,
                )
        case _:
            raise ConfigurationError(f"Unknown driver instance type: {type(instance.root)}")


def _import_client_class(driver_type: str) -> tuple[Any, str]:
    driver_class = import_class(driver_type, [], True)
    try:
        client_path = driver_class.client()
    except Exception as exc:
        raise ConfigurationError(
            f"Driver '{driver_type}' did not provide a client class: {exc}"
        ) from exc
    if not isinstance(client_path, str) or not client_path:
        raise ConfigurationError(f"Driver '{driver_type}' did not provide a client class path")
    return import_class(client_path, [], True), client_path


def _check_ref(ref: str) -> None:
    if not ref or any(part == "" for part in ref.split(".")):
        raise ConfigurationError(f"Client overlay ref {ref!r} is invalid")


def _follow_ref(ref: str, concrete: set[str], ref_targets: dict[str, str]) -> str:
    """Return the terminal path of a ref chain.

    The terminal is a concrete overlay path when the chain stays in the export.
    Otherwise it is a name to look up on the session client.
    """
    _check_ref(ref)
    seen: list[str] = []
    current = ref
    while current not in concrete:
        if current in seen:
            cycle = " -> ".join([*seen, current])
            raise ConfigurationError(f"Client overlay ref cycle: {cycle}")
        nxt = ref_targets.get(current)
        if nxt is None:
            return current
        seen.append(current)
        current = nxt
    return current


def _client_for_ref(
    ref: str,
    concrete: set[str],
    ref_targets: dict[str, str],
    registry: dict[str, DriverClient],
    session: _HasChildren | None,
) -> DriverClient:
    terminal = _follow_ref(ref, concrete, ref_targets)
    if terminal in concrete:
        return registry[terminal]
    found = _lookup_client(session, terminal)
    if found is None:
        raise ConfigurationError(
            f"Client overlay ref {ref!r} references nonexistent driver {terminal!r}"
        )
    return found


def _lookup_client(root: _HasChildren | None, path: str) -> DriverClient | None:
    if root is None:
        return None
    current: _HasChildren = root
    found: DriverClient | None = None
    for part in path.split("."):
        child = current.children.get(part)
        if not isinstance(child, DriverClient):
            return None
        found = child
        current = child
    return found


def _client_config_kwargs(client_class: Any, config: dict[str, Any]) -> dict[str, Any]:
    try:
        names = {item.name for item in fields(client_class) if item.init}
    except TypeError:
        names = set()
    return {key: value for key, value in config.items() if key in names and key not in _MANAGED_FIELDS}


def _make_client(
    client_class: Any,
    client_path: str,
    name: str,
    path: str,
    description: str | None,
    methods_description: dict[str, str],
    config: dict[str, Any],
    portal: BlockingPortal,
    stack: ExitStack,
) -> DriverClient:
    return client_class(
        stub=LocalOnlyStub(path),
        portal=portal,
        stack=stack.enter_context(ExitStack()),
        children={},
        description=description,
        methods_description=dict(methods_description),
        labels={
            "jumpstarter.dev/client": client_path,
            "jumpstarter.dev/name": name,
        },
        **_client_config_kwargs(client_class, config),
    )
