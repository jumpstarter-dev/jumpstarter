"""Unit tests for DriverRegistry."""

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from jumpstarter.exporter.registry import DriverRegistry, _UUID_METADATA_KEY


def _make_context(uuid: str | None = None):
    """Create a mock gRPC ServicerContext with optional UUID metadata."""
    ctx = MagicMock(spec=grpc.aio.ServicerContext)
    metadata = []
    if uuid is not None:
        metadata.append((_UUID_METADATA_KEY, uuid))
    ctx.invocation_metadata.return_value = metadata
    # context.abort is an async method on grpc.aio.ServicerContext;
    # simulate it raising to stop execution (as real gRPC does).
    ctx.abort = AsyncMock(side_effect=_AbortError)
    return ctx


class _AbortError(Exception):
    """Sentinel raised by mocked context.abort()."""


SERVICE_POWER = "jumpstarter.interfaces.power.v1.PowerInterface"
SERVICE_SERIAL = "jumpstarter.interfaces.serial.v1.SerialInterface"


# ── Empty registry ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_empty_registry_resolve_aborts_not_found():
    """Resolving from an empty registry aborts with NOT_FOUND."""
    reg = DriverRegistry()
    ctx = _make_context()

    with pytest.raises(_AbortError):
        await reg.resolve(ctx, SERVICE_POWER)

    ctx.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND,
        f"no driver registered for {SERVICE_POWER}",
    )


def test_empty_registry_services_property():
    """services property returns empty dict on fresh registry."""
    reg = DriverRegistry()
    assert reg.services == {}


# ── Single-instance default routing ─────────────────────────────────


@pytest.mark.anyio
async def test_single_instance_no_uuid_returns_driver():
    """When one driver is registered, resolve returns it without UUID metadata."""
    reg = DriverRegistry()
    driver = object()
    reg.register("uuid-1", SERVICE_POWER, driver)

    ctx = _make_context()  # no UUID metadata
    result = await reg.resolve(ctx, SERVICE_POWER)
    assert result is driver


@pytest.mark.anyio
async def test_single_instance_with_uuid_returns_driver():
    """When one driver is registered and UUID is provided, resolve returns it."""
    reg = DriverRegistry()
    driver = object()
    reg.register("uuid-1", SERVICE_POWER, driver)

    ctx = _make_context(uuid="uuid-1")
    result = await reg.resolve(ctx, SERVICE_POWER)
    assert result is driver


# ── UUID resolution ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_uuid_resolution_correct_driver():
    """UUID metadata selects the correct driver among multiple."""
    reg = DriverRegistry()
    d1, d2 = object(), object()
    reg.register("uuid-1", SERVICE_POWER, d1)
    reg.register("uuid-2", SERVICE_POWER, d2)

    ctx = _make_context(uuid="uuid-2")
    result = await reg.resolve(ctx, SERVICE_POWER)
    assert result is d2


@pytest.mark.anyio
async def test_uuid_resolution_first_driver():
    """UUID metadata can select the first registered driver."""
    reg = DriverRegistry()
    d1, d2 = object(), object()
    reg.register("uuid-1", SERVICE_POWER, d1)
    reg.register("uuid-2", SERVICE_POWER, d2)

    ctx = _make_context(uuid="uuid-1")
    result = await reg.resolve(ctx, SERVICE_POWER)
    assert result is d1


# ── Unknown UUID (NOT_FOUND) ────────────────────────────────────────


@pytest.mark.anyio
async def test_unknown_uuid_aborts_not_found():
    """Providing a UUID that doesn't exist aborts with NOT_FOUND."""
    reg = DriverRegistry()
    reg.register("uuid-1", SERVICE_POWER, object())

    ctx = _make_context(uuid="uuid-nonexistent")
    with pytest.raises(_AbortError):
        await reg.resolve(ctx, SERVICE_POWER)

    ctx.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND,
        f"driver uuid-nonexistent not found for {SERVICE_POWER}",
    )


@pytest.mark.anyio
async def test_unknown_service_aborts_not_found():
    """Resolving a service with no registrations aborts with NOT_FOUND."""
    reg = DriverRegistry()
    reg.register("uuid-1", SERVICE_POWER, object())

    ctx = _make_context()
    with pytest.raises(_AbortError):
        await reg.resolve(ctx, SERVICE_SERIAL)

    ctx.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND,
        f"no driver registered for {SERVICE_SERIAL}",
    )


# ── Multi-instance disambiguation (FAILED_PRECONDITION) ────────────


@pytest.mark.anyio
async def test_multi_instance_no_uuid_aborts_failed_precondition():
    """Multiple drivers for a service without UUID aborts with FAILED_PRECONDITION."""
    reg = DriverRegistry()
    reg.register("uuid-1", SERVICE_POWER, object())
    reg.register("uuid-2", SERVICE_POWER, object())

    ctx = _make_context()  # no UUID
    with pytest.raises(_AbortError):
        await reg.resolve(ctx, SERVICE_POWER)

    ctx.abort.assert_awaited_once()
    call_args = ctx.abort.call_args
    assert call_args[0][0] == grpc.StatusCode.FAILED_PRECONDITION
    assert "multiple drivers for" in call_args[0][1]
    assert "uuid-1" in call_args[0][1]
    assert "uuid-2" in call_args[0][1]


# ── Cross-service isolation ─────────────────────────────────────────


@pytest.mark.anyio
async def test_different_services_isolated():
    """Drivers registered under different services don't interfere."""
    reg = DriverRegistry()
    power_driver = object()
    serial_driver = object()
    reg.register("uuid-p", SERVICE_POWER, power_driver)
    reg.register("uuid-s", SERVICE_SERIAL, serial_driver)

    ctx_power = _make_context()
    assert await reg.resolve(ctx_power, SERVICE_POWER) is power_driver

    ctx_serial = _make_context()
    assert await reg.resolve(ctx_serial, SERVICE_SERIAL) is serial_driver


@pytest.mark.anyio
async def test_uuid_scoped_to_service():
    """UUID lookup is scoped to the requested service, not global."""
    reg = DriverRegistry()
    power_driver = object()
    serial_driver = object()
    reg.register("uuid-p", SERVICE_POWER, power_driver)
    reg.register("uuid-s", SERVICE_SERIAL, serial_driver)

    # uuid-s exists globally but not under SERVICE_POWER
    ctx = _make_context(uuid="uuid-s")
    with pytest.raises(_AbortError):
        await reg.resolve(ctx, SERVICE_POWER)

    ctx.abort.assert_awaited_once_with(
        grpc.StatusCode.NOT_FOUND,
        f"driver uuid-s not found for {SERVICE_POWER}",
    )


# ── Registration internals ──────────────────────────────────────────


def test_register_populates_by_uuid():
    """register() stores the driver in the internal _by_uuid mapping."""
    reg = DriverRegistry()
    driver = object()
    reg.register("uuid-1", SERVICE_POWER, driver)

    assert "uuid-1" in reg._by_uuid
    assert reg._by_uuid["uuid-1"] == (SERVICE_POWER, driver)


def test_services_property_reflects_registrations():
    """services property returns a snapshot of service -> {uuid: driver}."""
    reg = DriverRegistry()
    d1, d2 = object(), object()
    reg.register("uuid-1", SERVICE_POWER, d1)
    reg.register("uuid-2", SERVICE_POWER, d2)
    reg.register("uuid-3", SERVICE_SERIAL, object())

    services = reg.services
    assert set(services.keys()) == {SERVICE_POWER, SERVICE_SERIAL}
    assert len(services[SERVICE_POWER]) == 2
    assert len(services[SERVICE_SERIAL]) == 1


def test_services_property_returns_shallow_copy():
    """services property returns a shallow copy of the outer dict."""
    reg = DriverRegistry()
    reg.register("uuid-1", SERVICE_POWER, object())

    s1 = reg.services
    # Mutating the outer dict should not affect internals
    s1["injected_service"] = {}
    s2 = reg.services
    assert "injected_service" not in s2


# ── Concurrent access safety ────────────────────────────────────────


@pytest.mark.anyio
async def test_concurrent_register_and_resolve():
    """Multiple registrations followed by resolves are consistent.

    DriverRegistry uses plain dicts (no locks) which is safe in CPython
    due to the GIL for dict operations. This test verifies that many
    registrations and resolves produce consistent results.
    """
    reg = DriverRegistry()
    drivers = {}
    for i in range(100):
        uid = f"uuid-{i}"
        d = object()
        drivers[uid] = d
        reg.register(uid, SERVICE_POWER, d)

    # Resolve each one by UUID
    for uid, expected in drivers.items():
        ctx = _make_context(uuid=uid)
        assert await reg.resolve(ctx, SERVICE_POWER) is expected


# ── Edge cases ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_register_same_uuid_twice_overwrites():
    """Re-registering the same UUID overwrites the previous driver."""
    reg = DriverRegistry()
    d1 = object()
    d2 = object()
    reg.register("uuid-1", SERVICE_POWER, d1)
    reg.register("uuid-1", SERVICE_POWER, d2)

    ctx = _make_context(uuid="uuid-1")
    assert await reg.resolve(ctx, SERVICE_POWER) is d2


@pytest.mark.anyio
async def test_empty_uuid_metadata_treated_as_absent():
    """Empty string UUID in metadata is treated as no UUID provided."""
    reg = DriverRegistry()
    driver = object()
    reg.register("uuid-1", SERVICE_POWER, driver)

    # Empty string is falsy, so should fall through to single-instance default
    ctx = _make_context(uuid="")
    result = await reg.resolve(ctx, SERVICE_POWER)
    assert result is driver
