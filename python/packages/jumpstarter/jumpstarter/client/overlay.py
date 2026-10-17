"""A shell-owned local exporter, composed with the lab exporter on the client."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager, contextmanager
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

import yaml
from anyio.from_thread import start_blocking_portal
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from jumpstarter.client.base import DriverClient
from jumpstarter.common import ExporterStatus
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1DriverInstance,
    ExporterConfigV1Alpha1DriverInstanceProxy,
)

# Carry the shell-start ref snapshot in the root report. Clients need only the
# overlay socket, not the YAML file or the driver's constructor configuration.
_REFS_LABEL = "jumpstarter.dev/overlay-refs"


class ClientOverlayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export: dict[str, ExporterConfigV1Alpha1DriverInstance]


def _load_overlay_export(path: str):
    resolved = Path(path).expanduser().resolve()
    try:
        config = ClientOverlayConfig.model_validate(yaml.safe_load(resolved.read_text()))
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Failed to load client overlay '{resolved}': {exc}") from exc
    if not config.export:
        raise ConfigurationError("Client overlay export section is empty")
    return resolved, config.export


def _resolve_refs(refs: dict[str, str]) -> dict[str, str]:
    for path in (*refs, *refs.values()):
        if not path or any(not part for part in path.split(".")):
            raise ConfigurationError(f"Client overlay ref {path!r} is invalid")
    resolved = {}
    for path, target in refs.items():
        seen = {path}
        while target in refs:
            if target in seen:
                raise ConfigurationError(f"Client overlay ref cycle at {target!r}")
            seen.add(target)
            target = refs[target]
        resolved[path] = target
    return resolved


def _local_export(export: dict[str, ExporterConfigV1Alpha1DriverInstance]):
    """Keep local refs in the driver tree; carry remote refs to the clients."""
    children_by_path = {}
    refs = {}

    def copy_children(nodes, prefix):
        children = {}
        children_by_path[prefix] = children
        for name, instance in nodes.items():
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(instance.root, ExporterConfigV1Alpha1DriverInstanceProxy):
                refs[path] = instance.root.ref
            else:
                root = instance.root.model_copy(update={"children": copy_children(instance.root.children, path)})
                children[name] = instance.model_copy(update={"root": root})
        return children

    local = copy_children(export, "")
    refs = _resolve_refs(refs)
    graph = {path: [] for path in children_by_path}
    for path in children_by_path:
        if path:
            graph[path.rpartition(".")[0]].append(path)
    for path, target in refs.items():
        parent, _, name = path.rpartition(".")
        if target in children_by_path:
            graph[parent].append(target)
            children_by_path[parent][name] = ExporterConfigV1Alpha1DriverInstance.model_validate({"ref": target})
        else:
            # Keep required child names visible to driver constructors. The
            # client replaces these placeholders with the remote clients.
            children_by_path[parent][name] = ExporterConfigV1Alpha1DriverInstance.model_validate({"children": {}})
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as exc:
        raise ConfigurationError("Client overlay driver children form a cycle") from exc
    return local, refs


def prepare_client_overlay(path: str) -> str:
    """Validate the overlay before acquiring a lease or launching the shell."""
    resolved, export = _load_overlay_export(path)
    _local_export(export)
    return str(resolved)


@asynccontextmanager
async def _serve_client_overlay_async(path: str):
    # Import at runtime to avoid the driver/client import cycle.
    from jumpstarter.exporter import Session

    _, export = _load_overlay_export(path)
    local, refs = _local_export(export)
    root = ExporterConfigV1Alpha1DriverInstance.model_validate({
        "type": "jumpstarter_driver_composite.driver.Composite",
        "config": {"labels": {_REFS_LABEL: json.dumps(refs)}},
        "children": local,
    }).instantiate()
    with Session(root_device=root) as session:
        session.update_status(ExporterStatus.LEASE_READY)
        async with session.serve_unix_async() as socket:
            yield str(socket)


@contextmanager
def serve_client_overlay(path: str | None):
    """Own the overlay drivers for the lifetime of the shell subprocess."""
    if path is None:
        yield None
        return
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(_serve_client_overlay_async(path)) as socket,
    ):
        yield socket


def _lookup(children: dict[str, DriverClient], path: str) -> DriverClient:
    try:
        for part in path.split("."):
            client = children[part]
            children = client.children
        return client
    except KeyError as exc:
        raise ConfigurationError(f"Client overlay references nonexistent driver {path!r}") from exc


def apply_client_overlay(client: DriverClient, overlay: DriverClient) -> None:
    """Compose clients with silent whole-driver precedence for the overlay."""
    try:
        refs = TypeAdapter(dict[str, str]).validate_json(overlay.labels.get(_REFS_LABEL, "{}"), strict=True)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid client overlay refs: {exc}") from exc
    refs = _resolve_refs(refs)
    children = client.children | overlay.children
    resolved = {
        _lookup(overlay.children, path).uuid: _lookup(children, target)
        for path, target in refs.items()
    }
    # Drivers can share refs with children created in __post_init__. Match by
    # UUID throughout the reported tree, not just at the original YAML paths.
    # ponytail: remote refs are client-side only; add a driver-API bridge if local driver methods need them.
    pending = [overlay]
    seen = set()
    while pending:
        parent = pending.pop()
        if id(parent) in seen or parent.stub is not overlay.stub:
            continue
        seen.add(id(parent))
        for name, child in parent.children.items():
            child = resolved.get(child.uuid, child)
            parent.children[name] = child
            pending.append(child)
    client.children.update(overlay.children)
