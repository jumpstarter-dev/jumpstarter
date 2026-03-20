"""Introspection utilities for Click CLI trees and driver object trees."""

from __future__ import annotations

import inspect
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


def walk_click_tree(cmd: click.BaseCommand, path: list[str] | None = None) -> dict[str, Any]:
    """Recursively walk a Click command tree and return structured JSON.

    Returns command names, help text, parameters (with types and defaults),
    and nested subcommands.
    """
    path = path or []
    result: dict[str, Any] = {
        "name": cmd.name,
        "help": cmd.help,
        "params": [
            {
                "name": p.name,
                "type": str(p.type),
                "help": getattr(p, "help", None),
                "required": getattr(p, "required", False),
                "default": p.default if p.default is not None else None,
            }
            for p in cmd.params
            if not getattr(p, "hidden", False) and p.name != "help"
        ],
    }
    if isinstance(cmd, click.Group):
        result["subcommands"] = {
            name: walk_click_tree(sub, path + [name])
            for name, sub in cmd.commands.items()
        }
    return result


# Methods on the base DriverClient that should be excluded from introspection
_BASE_METHODS = {
    "call",
    "streamingcall",
    "stream",
    "stream_async",
    "log_stream",
    "log_stream_async",
    "open_stream",
    "close",
    "reset",
    "cli",
    "call_async",
    "streamingcall_async",
    "report",
    "report_async",
    "get_status_async",
    "end_session_async",
    "status_monitor_async",
}

# Methods inherited from base classes that add noise to driver listings
_INTERNAL_METHODS = {
    "check_exporter_status",
    "resource_async",
    "wait_for_hook_complete_monitored",
    "wait_for_hook_status",
    "wait_for_lease_ready",
    "wait_for_lease_ready_monitored",
}


def _get_public_method_names(obj: Any) -> list[str]:
    """Return public method names for a driver instance, excluding base internals.

    Uses getmembers_static to avoid triggering property descriptors, which can
    make gRPC calls that fail when invoked from the event loop thread.
    """
    names = []
    try:
        members = inspect.getmembers_static(obj)
    except Exception:
        logger.debug("inspect.getmembers_static failed for %s", type(obj).__name__, exc_info=True)
        return names
    for name, value in members:
        if name.startswith("_") or name in _BASE_METHODS or name in _INTERNAL_METHODS:
            continue
        if isinstance(value, property):
            continue
        if not callable(value):
            continue
        try:
            inspect.signature(value)
        except (ValueError, TypeError, AttributeError):
            continue
        names.append(name)
    return names


def list_drivers(client: Any, prefix: str = "client") -> list[dict[str, Any]]:
    """Flatten the driver client tree into a list with dot-separated paths.

    Returns path, class name, description, and method names for each driver.
    """
    cls = type(client)
    results = [
        {
            "path": prefix,
            "class": f"{cls.__module__}.{cls.__qualname__}",
            "description": getattr(client, "description", None),
            "methods": _get_public_method_names(client),
        }
    ]
    children = getattr(client, "children", {})
    for name, child in children.items():
        results.extend(list_drivers(child, f"{prefix}.{name}"))
    return results


def _inspect_method(name: str, method: Any, driver_path: list[str]) -> dict[str, Any] | None:
    """Build a method descriptor dict, or return None if the method can't be inspected."""
    try:
        sig = inspect.signature(method)
    except (ValueError, TypeError, AttributeError):
        return None

    is_streaming = False
    try:
        source = inspect.getsource(method)
        is_streaming = "streamingcall" in source
    except (OSError, TypeError):
        pass

    params = [
        {
            "name": pname,
            "annotation": str(p.annotation) if p.annotation != inspect.Parameter.empty else None,
            "default": str(p.default) if p.default != inspect.Parameter.empty else None,
        }
        for pname, p in sig.parameters.items()
        if pname != "self"
    ]

    attr_path = ".".join(driver_path)
    call_args = ", ".join(f"{p['name']}=..." for p in params)
    method_call = f"client.{attr_path}.{name}({call_args})"

    return {
        "name": name,
        "signature": str(sig),
        "docstring": inspect.getdoc(method),
        "parameters": params,
        "return_type": str(sig.return_annotation) if sig.return_annotation != inspect.Signature.empty else None,
        "is_streaming": is_streaming,
        "call_example": (
            "from jumpstarter.utils.env import env\n\n"
            "with env() as client:\n"
            f"    {method_call}"
        ),
    }


def get_driver_methods(client: Any, driver_path: list[str]) -> dict[str, Any]:
    """Inspect a specific driver client at the given path in the children tree.

    Uses Python inspect to return detailed method information for all public
    methods defined on the concrete driver class.
    """
    target = client
    for key in driver_path:
        children = getattr(target, "children", {})
        if key not in children:
            raise KeyError(f"Driver path component '{key}' not found. Available: {list(children.keys())}")
        target = children[key]

    cls = type(target)
    try:
        members = inspect.getmembers_static(target)
    except Exception:
        logger.debug("inspect.getmembers_static failed for %s", cls.__name__, exc_info=True)
        members = []

    methods = []
    for name, value in members:
        if name.startswith("_") or name in _BASE_METHODS or name in _INTERNAL_METHODS:
            continue
        if isinstance(value, property) or not callable(value):
            continue
        info = _inspect_method(name, value, driver_path)
        if info is not None:
            methods.append(info)

    return {
        "class": f"{cls.__module__}.{cls.__qualname__}",
        "driver_path": driver_path,
        "methods": methods,
    }
