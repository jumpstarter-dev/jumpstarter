"""MCP tools for lease and exporter management.

The controller operations (list/create/delete exporters + leases) run on the Rust core via
the FFI ``jumpstarter_core.ControllerSession`` (the same controller client the ``jmp`` CLI
uses) — no Python gRPC. Each function takes a connected ``ControllerSession`` and shapes the
JSON it returns into the MCP tool result.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _iso(epoch: float | None) -> str | None:
    """Format a Unix epoch (seconds) as an ISO-8601 UTC timestamp, or None."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _duration_str(seconds: float | None) -> str | None:
    """Format a duration in seconds as a ``H:MM:SS`` string (matching ``str(timedelta)``)."""
    if seconds is None:
        return None
    return str(timedelta(seconds=seconds))


def _lease_status(lease: dict) -> str:
    """Derive a human-readable status from a lease's conditions (list of {type, status})."""
    for cond in lease.get("conditions") or []:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return "ready"
        if cond.get("type") == "Pending" and cond.get("status") == "True":
            return "pending"
        if cond.get("type") == "Unsatisfiable" and cond.get("status") == "True":
            return "unsatisfiable"
    return "unknown"


def _lease_summary(lease: dict) -> dict:
    """The per-exporter lease summary embedded in ``list_exporters`` output."""
    return {
        "name": lease["name"],
        "client": lease.get("client"),
        "status": _lease_status(lease),
        "duration": _duration_str(lease.get("duration_seconds")),
        "begin_time": _iso(lease.get("begin_time_epoch")),
        "end_time": _iso(lease.get("end_time_epoch")),
    }


async def list_exporters(
    session: Any,
    selector: str | None = None,
    include_leases: bool = True,
    include_online: bool = True,
) -> list[dict]:
    """List exporters from the controller, optionally attaching each one's active lease."""
    exporters = json.loads(await session.list_exporters(selector))

    active_by_exporter: dict[str, dict] = {}
    if include_leases:
        leases = json.loads(await session.list_leases(None, True, None))
        for lease in leases:
            exporter = lease.get("exporter")
            if exporter and _lease_status(lease) == "ready":
                active_by_exporter[exporter] = lease

    result = []
    for exporter in exporters:
        entry: dict = {
            "name": exporter["name"],
            "labels": dict(exporter.get("labels") or {}),
        }
        if include_online:
            entry["online"] = exporter.get("online", False)
        if exporter.get("status") is not None:
            entry["status"] = exporter["status"]
        if include_leases:
            lease = active_by_exporter.get(exporter["name"])
            entry["lease"] = _lease_summary(lease) if lease else None
        result.append(entry)
    return result


async def list_leases(
    session: Any,
    selector: str | None = None,
    show_all: bool = False,
) -> list[dict]:
    """List leases from the controller."""
    leases = json.loads(await session.list_leases(selector, not show_all, None))
    return [
        {
            "name": lease["name"],
            "client": lease.get("client"),
            "exporter": lease.get("exporter"),
            "selector": lease.get("selector"),
            "status": _lease_status(lease),
            "begin_time": _iso(lease.get("begin_time_epoch")),
            "end_time": _iso(lease.get("end_time_epoch")),
            "duration": _duration_str(lease.get("duration_seconds")),
        }
        for lease in leases
    ]


async def create_lease(
    session: Any,
    duration_seconds: int = 1800,
    selector: str | None = None,
    exporter_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> dict:
    """Create a new lease."""
    name = await session.create_lease(duration_seconds, selector, exporter_name, tags or {})
    return {
        "name": name,
        "status": "created",
        "duration_seconds": duration_seconds,
        "selector": selector,
        "exporter_name": exporter_name,
        "tags": tags,
    }


async def delete_lease(
    session: Any,
    lease_id: str,
) -> dict:
    """Delete (release) a lease by name."""
    await session.release_lease(lease_id)
    return {"name": lease_id, "status": "deleted"}
