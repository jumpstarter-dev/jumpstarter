"""Client-only overlay built from an exporter config's export section.

The overlay instantiates driver *clients* (including nested children and refs)
and never starts the corresponding drivers. Server-side calls on those clients
fail.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

from anyio.from_thread import BlockingPortal
from jumpstarter_driver_composite.client import CompositeClient
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
    """
    resolved = Path(path).expanduser().resolve()
    try:
        config = ExporterConfigV1Alpha1.load_path(resolved)
    except (OSError, ValidationError) as exc:
        raise ConfigurationError(f"Failed to load client overlay '{resolved}': {exc}") from exc
    validate_client_overlay(config.export)
    return str(resolved)


def validate_client_overlay(export: dict[str, ExporterConfigV1Alpha1DriverInstance]) -> None:
    """Fail if the export section cannot be built as a client-only tree."""
    if not export:
        raise ConfigurationError("Client overlay export section is empty")
    concrete, refs = _index_export(export)
    for ref in refs.values():
        _resolve_ref(ref, concrete, refs)


def build_client_overlay(
    export: dict[str, ExporterConfigV1Alpha1DriverInstance],
    portal: BlockingPortal,
    stack: ExitStack,
) -> CompositeClient:
    """Build a composite client whose children are the export section."""
    validate_client_overlay(export)
    registry: dict[str, DriverClient] = {}
    pending: list[tuple[str, str, str]] = []
    ref_targets: dict[str, str] = {}
    root = cast(CompositeClient, _make_client(
        CompositeClient,
        _COMPOSITE_CLIENT,
        "overlay",
        "overlay",
        None,
        {},
        {},
        portal,
        stack,
    ))
    registry[""] = root
    for name, instance in export.items():
        _add_node(root.children, "", name, instance, name, portal, stack, registry, pending, ref_targets)
    concrete = set(registry) - {""}
    for parent_path, name, ref in pending:
        registry[parent_path].children[name] = registry[_resolve_ref(ref, concrete, ref_targets)]
    return root


def apply_client_overlay(
    client: DriverClient,
    export: dict[str, ExporterConfigV1Alpha1DriverInstance],
    portal: BlockingPortal,
    stack: ExitStack,
) -> None:
    """Merge export clients onto ``client``. Existing names are replaced."""
    overlay = build_client_overlay(export, portal, stack)
    for name, child in overlay.children.items():
        if name in client.children:
            print(
                f"Warning: client overlay replaced existing client '{name}'",
                file=sys.stderr,
            )
        client.children[name] = child


def apply_client_overlay_from_path(
    client: DriverClient,
    path: str,
    portal: BlockingPortal,
    stack: ExitStack,
) -> None:
    """Load ``path`` and merge its export section onto ``client``."""
    config = ExporterConfigV1Alpha1.load_path(Path(path))
    apply_client_overlay(client, config.export, portal, stack)


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
                CompositeClient,
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


def _resolve_ref(ref: str, concrete: set[str], ref_targets: dict[str, str]) -> str:
    _check_ref(ref)
    seen: list[str] = []
    current = ref
    while current not in concrete:
        if current in seen:
            cycle = " -> ".join([*seen, current])
            raise ConfigurationError(f"Client overlay ref cycle: {cycle}")
        nxt = ref_targets.get(current)
        if nxt is None:
            raise ConfigurationError(
                f"Client overlay ref {ref!r} references nonexistent driver {current!r}"
            )
        seen.append(current)
        current = nxt
    return current


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
