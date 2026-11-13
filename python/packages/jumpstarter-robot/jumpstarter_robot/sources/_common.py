"""Helpers shared by the device tree sources."""

from __future__ import annotations

import logging

from ..tree import COMPOSITE_CLIENT, PYTHON, DeviceNode, NodeKind

logger = logging.getLogger("jumpstarter_robot.sources")

CLIENT_LABEL = "jumpstarter.dev/client"
NAME_LABEL = "jumpstarter.dev/name"
INTERFACE_LABEL = "jumpstarter.dev/interface"
STUB_CLIENT = "jumpstarter.client.base.StubDriverClient"


class SourceError(ValueError):
    """A device tree source cannot be read."""


def class_path(spec: str) -> str:
    """Normalize ``module:Class`` to the dotted ``module.Class`` the resource uses."""
    spec = spec.strip()
    module, sep, name = spec.partition(":")
    return f"{module}.{name}" if sep else spec


def is_composite_client(client: str | None) -> bool:
    return client == COMPOSITE_CLIENT


def driver_node(
    path: list[str],
    client: str | None,
    *,
    kind: NodeKind | None = None,
    required: bool = True,
    methods: list[str] | None = None,
    interface: str | None = None,
    driver_type: str | None = None,
    children: list[DeviceNode] | None = None,
) -> DeviceNode:
    """A driver node, or a composite one when its client is the composite client."""
    if kind is None:
        kind = "composite" if is_composite_client(client) else "driver"
    return DeviceNode(
        name=path[-1] if path else "",
        path=list(path),
        kind=kind,
        interface=interface,
        driver_type=driver_type,
        bindings={PYTHON: client} if client else {},
        required=required,
        methods=list(methods or []),
        children=list(children or []),
    )


def opaque_node(path: list[str], reason: str, warnings: list[str], *, required: bool = True) -> DeviceNode:
    """A node no client is known for; it is recorded with a warning and skipped by the renderer."""
    message = f"{'.'.join(path) or '(root)'}: {reason}"
    warnings.append(message)
    logger.warning("%s", message)
    return DeviceNode(name=path[-1] if path else "", path=list(path), kind="opaque", required=required, reason=reason)
