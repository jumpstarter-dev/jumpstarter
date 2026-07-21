"""Tests for MCP lease tools deprecated_labels coverage."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from .leases import create_lease, list_exporters, list_leases
from jumpstarter.client.grpc import Exporter, ExporterList, Lease, LeaseList


def _make_exporter(name="exp-1", deprecated_labels=None):
    return Exporter(
        namespace="default",
        name=name,
        labels={"board": "rpi4"},
        deprecated_labels=deprecated_labels or {},
    )


def _make_lease(name="lease-1", deprecated_labels=None):
    return Lease(
        namespace="default",
        name=name,
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=30),
        effective_duration=None,
        begin_time=None,
        client="test-client",
        exporter="test-exporter",
        conditions=[],
        effective_begin_time=None,
        effective_end_time=None,
        deprecated_labels=deprecated_labels or {},
    )


@pytest.mark.asyncio
async def test_list_exporters_includes_deprecated_labels():
    config = AsyncMock()
    config.list_exporters.return_value = ExporterList(
        exporters=[_make_exporter(deprecated_labels={"old-key": "Use new-key"})],
        next_page_token=None,
    )
    result = await list_exporters(config)
    assert result[0]["deprecated_labels"] == {"old-key": "Use new-key"}


@pytest.mark.asyncio
async def test_list_exporters_omits_empty_deprecated_labels():
    config = AsyncMock()
    config.list_exporters.return_value = ExporterList(
        exporters=[_make_exporter()],
        next_page_token=None,
    )
    result = await list_exporters(config)
    assert "deprecated_labels" not in result[0]


@pytest.mark.asyncio
async def test_list_leases_includes_deprecated_labels():
    config = AsyncMock()
    config.list_leases.return_value = LeaseList(
        leases=[_make_lease(deprecated_labels={"legacy-board": "Use board"})],
        next_page_token=None,
    )
    result = await list_leases(config)
    assert result[0]["deprecated_labels"] == {"legacy-board": "Use board"}


@pytest.mark.asyncio
async def test_list_leases_omits_empty_deprecated_labels():
    config = AsyncMock()
    config.list_leases.return_value = LeaseList(
        leases=[_make_lease()],
        next_page_token=None,
    )
    result = await list_leases(config)
    assert "deprecated_labels" not in result[0]


@pytest.mark.asyncio
async def test_create_lease_includes_deprecated_labels():
    lease = _make_lease(deprecated_labels={"old-pool": "Removed in v2.0"})
    config = AsyncMock()
    config.create_lease.return_value = lease
    result = await create_lease(config, selector="old-pool=staging")
    assert result["deprecated_labels"] == {"old-pool": "Removed in v2.0"}


@pytest.mark.asyncio
async def test_create_lease_omits_empty_deprecated_labels():
    lease = _make_lease()
    config = AsyncMock()
    config.create_lease.return_value = lease
    result = await create_lease(config, selector="board=rpi4")
    assert "deprecated_labels" not in result
