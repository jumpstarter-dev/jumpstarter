"""Machine-readable introspection of a driver-client tree, emitted as JSON.

Invoked as ``j introspect <drivers|explore|driver-methods> [args...]`` (intercepted in
``j.py`` before the normal driver-client CLI passthrough). The Rust MCP server shells out to
this for its explore/drivers/driver_methods tools — the driver clients are Python, so their
Click command trees + method signatures can only be introspected in-process here.

Ported from the deleted ``jumpstarter_mcp/introspect.py``.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import click
import click.core

logger = logging.getLogger(__name__)


def walk_click_tree(cmd: click.core.BaseCommand, path: list[str] | None = None) -> dict[str, Any]:  # ty: ignore[unresolved-attribute]
    """Recursively walk a Click command tree → command names, help, params, subcommands."""
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
        result["subcommands"] = {name: walk_click_tree(sub, path + [name]) for name, sub in cmd.commands.items()}
    return result


# Methods on the base DriverClient that should be excluded from introspection.
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

# Methods inherited from base classes that add noise to driver listings.
_INTERNAL_METHODS = {
    "check_exporter_status",
    "resource_async",
    "wait_for_hook_complete_monitored",
    "wait_for_hook_status",
    "wait_for_lease_ready",
    "wait_for_lease_ready_monitored",
}


def _get_public_method_names(obj: Any) -> list[str]:
    """Public method names for a driver instance, excluding base internals.

    Uses ``getmembers_static`` to avoid triggering property descriptors (which could make
    driver calls from the wrong thread).
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
        if isinstance(value, property) or not callable(value):
            continue
        try:
            inspect.signature(value)
        except (ValueError, TypeError, AttributeError):
            continue
        names.append(name)
    return names


def list_drivers(client: Any, _keys: list[str] | None = None) -> list[dict[str, Any]]:
    """Flatten the driver-client tree into a list with dot-separated paths."""
    keys = _keys or []
    cls = type(client)
    results = [
        {
            "path": f"client.{'.'.join(keys)}" if keys else "client",
            "driver_path": keys,
            "class": f"{cls.__module__}.{cls.__qualname__}",
            "description": getattr(client, "description", None),
            "methods": _get_public_method_names(client),
        }
    ]
    children = getattr(client, "children", {})
    for name, child in children.items():
        results.extend(list_drivers(child, keys + [name]))
    return results


def _inspect_method(name: str, method: Any, driver_path: list[str]) -> dict[str, Any] | None:
    """A method descriptor dict, or None if the method can't be inspected."""
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
        "call_example": ("from jumpstarter.utils.env import env\n\nwith env() as client:\n" f"    {method_call}"),
    }


def get_driver_methods(client: Any, driver_path: list[str]) -> dict[str, Any]:
    """Detailed method info for the driver client at ``driver_path`` in the children tree."""
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


def dispatch(client: Any, argv: list[str]) -> dict[str, Any]:
    """Run an introspection subcommand against a built driver client, returning a JSON-able dict.

    Subcommands: ``drivers`` | ``explore [PATH...]`` | ``driver-methods [PATH...]``.
    """
    if not argv:
        return {"error": "introspect: subcommand required (drivers|explore|driver-methods)"}
    sub, rest = argv[0], argv[1:]

    if sub == "drivers":
        return {"drivers": list_drivers(client)}

    if sub == "explore":
        if not hasattr(client, "cli"):
            return {"error": "Client does not have a CLI interface"}
        cli_cmd = client.cli()
        for name in rest:
            if isinstance(cli_cmd, click.Group) and name in cli_cmd.commands:
                cli_cmd = cli_cmd.commands[name]
            else:
                return {"error": f"Command '{name}' not found at this level"}
        return walk_click_tree(cli_cmd)

    if sub == "driver-methods":
        try:
            return get_driver_methods(client, rest)
        except KeyError as e:
            return {"error": str(e)}

    return {"error": f"unknown introspect subcommand: {sub}"}
