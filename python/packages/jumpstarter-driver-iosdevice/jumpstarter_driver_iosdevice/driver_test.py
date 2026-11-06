import plistlib
import struct
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import BrokenResourceError, ClosedResourceError

from .driver import IosDevice, IosPortForward, _get_value, _serial_at_port
from jumpstarter.common.exceptions import ConfigurationError

UDID = "00008110-001234567890001E"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"udid": ""},
        {"udid": "../phone"},
        {"udid": UDID, "usb_port": "1-2"},
        {"usb_port": "../../etc"},
        {"udid": UDID, "transport": "tcp"},
        {"transport": "network", "udid": UDID},
        {"transport": "network", "udid": UDID, "usbmux_host": "localhost", "usb_port": "1-2"},
        {"udid": UDID, "usbmux_host": "localhost"},
        {"udid": UDID, "kind": "simulator"},
        {"udid": UDID, "trust_mode": "unknown"},
        {"udid": UDID, "forward_ports": [62078]},
        *({"udid": UDID, "connect_timeout": value} for value in [0, -1, float("inf"), float("nan"), True, "10"]),
        *({"udid": UDID, "forward_ports": [value]} for value in [0, 65536, True, "8100"]),
        {"udid": UDID, "forward_ports": [8100, 8100]},
    ],
)
def test_invalid_configuration(config):
    with pytest.raises(ConfigurationError):
        IosDevice(**config)


def test_forwards_are_explicit_and_no_physical_power_child():
    driver = IosDevice(udid=UDID, forward_ports=[8100, 9100])
    assert set(driver.children) == {"port_8100", "port_9100"}
    child = driver.children["port_8100"]
    assert isinstance(child, IosPortForward) and child.device is driver
    with pytest.raises(ConfigurationError, match="conflicts"):
        IosDevice(udid=UDID, forward_ports=[8100], children={"port_8100": driver})


@pytest.mark.anyio
async def test_usb_port_resolves_again_after_hardware_swap():
    driver = IosDevice(usb_port="1-2")
    daemon = AsyncMock()
    other = "00008110-001234567890002E"
    daemon.list_devices.return_value = [
        {"DeviceID": 1, "Properties": {"SerialNumber": UDID, "ConnectionType": "USB"}},
        {"DeviceID": 2, "Properties": {"SerialNumber": other, "ConnectionType": "USB"}},
    ]
    with patch("jumpstarter_driver_iosdevice.driver._serial_at_port", side_effect=[UDID.replace("-", ""), other]):
        assert (await driver._resolve_device(daemon))["DeviceID"] == 1
        assert (await driver._resolve_device(daemon))["DeviceID"] == 2


@pytest.mark.anyio
async def test_usb_selector_ignores_wifi_copy():
    driver = IosDevice(udid=UDID)
    daemon = AsyncMock()
    daemon.list_devices.return_value = [{"Properties": {"SerialNumber": UDID, "ConnectionType": "Network"}}]
    with pytest.raises(RuntimeError, match="not present"):
        await driver._resolve_device(daemon)


@pytest.mark.anyio
async def test_ambiguous_network_device_is_refused():
    driver = IosDevice(transport="network", udid=UDID, usbmux_host="localhost")
    daemon = AsyncMock()
    daemon.list_devices.return_value = [
        {"DeviceID": device_id, "Properties": {"SerialNumber": UDID}} for device_id in (1, 2)
    ]
    with pytest.raises(RuntimeError, match="multiple"):
        await driver._resolve_device(daemon)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "version,present,reason",
    [
        ("17.3.1", False, "17.4+ required"),
        ("17.4", True, None),
        ("18.0.1", True, None),
        ("27.1", True, None),
        ("unknown", False, "cannot verify"),
    ],
)
async def test_version_floor(version, present, reason):
    driver = IosDevice(udid=UDID)
    with patch.object(
        driver, "_describe", AsyncMock(return_value={"udid": UDID, "model": "iPhone", "os_version": version})
    ):
        info = await driver.info()
        assert info["present"] is present
        assert info["protocols"] == ["usbmux"]
        if reason:
            assert reason in info["reason"]
            with pytest.raises(RuntimeError, match="required|verify"):
                await driver._require_device()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        OSError("missing daemon"),
        TimeoutError(),
        RuntimeError("device absent"),
        BrokenResourceError(),
        ClosedResourceError(),
    ],
)
async def test_unavailable_info_is_actionable(failure):
    driver = IosDevice(udid=UDID)
    with patch.object(driver, "_describe", AsyncMock(side_effect=failure)):
        info = await driver.info()
    assert info["present"] is False
    assert info["reason"].startswith("cannot access iOS device:")
    assert info["reason"].split(":", 1)[1].strip()


def test_missing_usb_port_reports_bench_visit():
    with patch("pathlib.Path.read_text", side_effect=FileNotFoundError), pytest.raises(RuntimeError, match="power on"):
        _serial_at_port("1-2")


@pytest.mark.anyio
async def test_forward_uses_selected_phone_port():
    driver = IosDevice(udid=UDID, forward_ports=[8100], trust_mode="passthrough")
    calls = []
    stream = object()

    @asynccontextmanager
    async def connect(udid, port):
        calls.append((udid, port))
        yield stream

    with (
        patch.object(driver, "_require_device", AsyncMock(return_value=UDID)),
        patch.object(driver, "_daemon") as daemon,
    ):
        daemon.return_value.connect_device = connect
        port_forward = driver.children["port_8100"]
        assert isinstance(port_forward, IosPortForward)
        async with port_forward.connect() as forwarded:
            assert forwarded is stream
    assert calls == [(UDID, 8100)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {"Request": "GetValue", "Error": "PasswordProtected"},
        {"Request": "Other"},
        {"Request": "GetValue", "Value": 27},
        [],
    ],
)
async def test_lockdown_invalid_responses(response):
    data = plistlib.dumps(response)
    reader = AsyncMock()
    reader.receive_exactly.side_effect = [struct.pack(">I", len(data)), data]
    with pytest.raises(RuntimeError):
        await _get_value(AsyncMock(), reader, "ProductVersion")


@pytest.mark.anyio
async def test_lockdown_length_bound_before_reading_payload():
    reader = AsyncMock()
    reader.receive_exactly.return_value = struct.pack(">I", 1024 * 1024 + 1)
    with pytest.raises(RuntimeError, match="length"):
        await _get_value(AsyncMock(), reader, "ProductVersion")
    reader.receive_exactly.assert_awaited_once_with(4)


@pytest.mark.anyio
async def test_malformed_daemon_records_are_not_selected():
    driver = IosDevice(udid=UDID)
    daemon = AsyncMock()
    daemon.list_devices.return_value = [
        {"DeviceID": 1, "Properties": None},
        {"Properties": {"SerialNumber": UDID, "ConnectionType": "USB"}},
    ]
    with pytest.raises(RuntimeError, match="not present"):
        await driver._resolve_device(daemon)


def test_usb_transport_restricts_daemon_to_usb_link():
    assert IosDevice(udid=UDID)._daemon().connection_type == "USB"
    assert IosDevice(usb_port="1-2")._daemon().connection_type == "USB"
    network = IosDevice(transport="network", udid=UDID, usbmux_host="localhost")
    assert network._daemon().connection_type is None


def test_exporter_owns_trust_by_default_and_raw_lockdown_requires_opt_in():
    assert IosDevice(udid=UDID).trust_mode == "exporter"
    assert IosDevice(udid=UDID, trust_mode="passthrough", forward_ports=[62078]).forward_ports == [62078]


@pytest.mark.anyio
async def test_broker_reused_within_generation_and_replaced_after_replug():
    driver = IosDevice(udid=UDID, forward_ports=[8100])
    device = {"DeviceID": 1, "Properties": {"SerialNumber": UDID, "ConnectionType": "USB"}}
    first, second = MagicMock(), MagicMock()
    first.aclose = AsyncMock()
    with (
        patch.object(driver, "_resolve_device", AsyncMock(return_value=device)),
        patch("jumpstarter_driver_iosdevice.trust.ExporterTrustBroker", side_effect=[first, second]) as factory,
    ):
        assert await driver._broker_for_device(UDID) is first
        assert await driver._broker_for_device(UDID) is first
        assert factory.call_count == 1
        assert factory.call_args.kwargs["device_id"] == 1
        assert factory.call_args.kwargs["allowed_ports"] == [8100]
        assert factory.call_args.kwargs["connection_type"] == "USB"
        device["DeviceID"] = 2
        assert await driver._broker_for_device(UDID) is second
        first.aclose.assert_awaited_once()
        assert factory.call_count == 2
    driver.close()
    second.invalidate.assert_called_once()
    assert driver._trust_broker is None


@pytest.mark.anyio
async def test_device_disappears_or_changes_during_broker_selection_revokes_old_trust():
    driver = IosDevice(usb_port="1-2")
    broker = MagicMock()
    driver._trust_broker = broker
    other = {"DeviceID": 2, "Properties": {"SerialNumber": "other-device"}}
    with (
        patch.object(driver, "_resolve_device", AsyncMock(return_value=other)),
        pytest.raises(RuntimeError, match="changed"),
    ):
        await driver._broker_for_device(UDID)
    broker.invalidate.assert_called_once()
    assert driver._trust_broker is None


@pytest.mark.parametrize("method", ["reset", "close"])
def test_session_lifecycle_invalidates_proxy_identity(method):
    driver = IosDevice(udid=UDID)
    broker = MagicMock()
    driver._trust_broker = broker
    driver._trust_generation = (UDID, 1)
    getattr(driver, method)()
    broker.invalidate.assert_called_once()
    assert driver._trust_broker is None
    assert driver._trust_generation is None


@pytest.mark.anyio
async def test_exporter_mode_routes_usbmux_and_forwards_through_shared_broker():
    driver = IosDevice(udid=UDID, forward_ports=[8100])
    broker, stream = MagicMock(), object()
    seen = []

    @asynccontextmanager
    async def proxy(udid):
        seen.append(("usbmux", udid))
        yield stream

    @asynccontextmanager
    async def forward(port):
        seen.append(("forward", port))
        yield stream

    broker.proxy, broker.open_forward = proxy, forward
    with (
        patch.object(driver, "_require_device", AsyncMock(return_value=UDID)),
        patch.object(driver, "_broker_for_device", AsyncMock(return_value=broker)) as get_broker,
    ):
        async with driver.connect_usbmux() as connection:
            assert connection is stream
        port_forward = driver.children["port_8100"]
        assert isinstance(port_forward, IosPortForward)
        async with port_forward.connect() as connection:
            assert connection is stream
    assert get_broker.await_count == 2
    assert seen == [("usbmux", UDID), ("forward", 8100)]


@pytest.mark.anyio
async def test_revoked_broker_rotates_even_when_device_id_is_reused():
    driver = IosDevice(udid=UDID)
    first, second = MagicMock(), MagicMock()
    first.is_active = False
    first.aclose = AsyncMock()
    driver._trust_broker, driver._trust_generation = first, (UDID, 1)
    device = {"DeviceID": 1, "Properties": {"SerialNumber": UDID}}
    with (
        patch.object(driver, "_resolve_device", AsyncMock(return_value=device)),
        patch("jumpstarter_driver_iosdevice.trust.ExporterTrustBroker", return_value=second),
    ):
        assert await driver._broker_for_device(UDID) is second
    first.aclose.assert_awaited_once()
