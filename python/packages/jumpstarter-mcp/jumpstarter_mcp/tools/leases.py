"""MCP tools for lease and exporter management."""

from __future__ import annotations

from datetime import timedelta

from jumpstarter.config.client import ClientConfigV1Alpha1


async def list_exporters(
    config: ClientConfigV1Alpha1,
    selector: str | None = None,
    include_leases: bool = True,
    include_online: bool = True,
) -> list[dict]:
    """List exporters from the controller."""
    result = await config.list_exporters(
        filter=selector,
        include_leases=include_leases,
        include_online=include_online,
        include_status=True,
    )
    exporters = []
    for exporter in result.exporters:
        entry: dict = {
            "name": exporter.name,
            "labels": dict(exporter.labels),
        }
        if include_online:
            entry["online"] = exporter.online
        if exporter.status is not None:
            entry["status"] = exporter.status.name
        if include_leases and exporter.lease:
            lease = exporter.lease
            entry["lease"] = {
                "name": lease.name,
                "client": lease.client,
                "status": _lease_status(lease),
                "duration": str(lease.duration) if lease.duration else None,
                "begin_time": lease.effective_begin_time.isoformat() if lease.effective_begin_time else None,
                "end_time": lease.effective_end_time.isoformat() if lease.effective_end_time else None,
            }
        elif include_leases:
            entry["lease"] = None
        exporters.append(entry)
    return exporters


async def list_leases(
    config: ClientConfigV1Alpha1,
    selector: str | None = None,
    show_all: bool = False,
) -> list[dict]:
    """List leases from the controller."""
    result = await config.list_leases(filter=selector, only_active=not show_all)
    leases = []
    for lease in result.leases:
        leases.append({
            "name": lease.name,
            "client": lease.client,
            "exporter": lease.exporter,
            "selector": lease.selector,
            "status": _lease_status(lease),
            "begin_time": lease.effective_begin_time.isoformat() if lease.effective_begin_time else None,
            "end_time": lease.effective_end_time.isoformat() if lease.effective_end_time else None,
            "duration": str(lease.duration) if lease.duration else None,
        })
    return leases


async def create_lease(
    config: ClientConfigV1Alpha1,
    duration_seconds: int = 1800,
    selector: str | None = None,
    exporter_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> dict:
    """Create a new lease."""
    duration = timedelta(seconds=duration_seconds)
    result = await config.create_lease(
        duration=duration,
        selector=selector,
        exporter_name=exporter_name,
        tags=tags,
    )
    return {
        "name": result.name,
        "status": "created",
        "duration_seconds": duration_seconds,
        "selector": selector,
        "exporter_name": exporter_name,
        "tags": tags,
    }


async def delete_lease(
    config: ClientConfigV1Alpha1,
    lease_id: str,
) -> dict:
    """Delete a lease."""
    await config.delete_lease(name=lease_id)
    return {"name": lease_id, "status": "deleted"}


def _lease_status(lease) -> str:
    """Extract a human-readable status from lease conditions."""
    conditions = getattr(lease, "conditions", [])
    for cond in conditions:
        if getattr(cond, "type", "") == "Ready" and getattr(cond, "status", "") == "True":
            return "ready"
        if getattr(cond, "type", "") == "Pending" and getattr(cond, "status", "") == "True":
            return "pending"
        if getattr(cond, "type", "") == "Unsatisfiable" and getattr(cond, "status", "") == "True":
            return "unsatisfiable"
    return "unknown"
