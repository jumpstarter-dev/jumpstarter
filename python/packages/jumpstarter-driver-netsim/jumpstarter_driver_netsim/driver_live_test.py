"""Live tests that run against a real netsim instance.

Run with: make pkg-test-jumpstarter-driver-netsim PYTEST_ARGS="-m live --live-netsim-host=HOST --live-netsim-port=PORT"
Skipped by default in CI.
"""

import time

import pytest
import requests

from .driver import Netsim, NetsimError

POLL_INTERVAL = 0.5
POLL_TIMEOUT = 10


@pytest.fixture
def live_drv(request):
    host = request.config.getoption("--live-netsim-host")
    port = request.config.getoption("--live-netsim-port")
    cli = request.config.getoption("--live-netsim-cli")
    return Netsim(host=host, port=port, netsim_cli=cli)


def _get_rx_count(drv, device_id="1"):
    """Get BLE rx count for a device from the raw device list."""
    for dev in drv._list_devices_raw():
        if str(dev.get("id")) == device_id:
            for chip in dev.get("chips", []):
                if "bt" in chip:
                    return chip["bt"]["lowEnergy"]["rxCount"]
    return None


def _poll_rx_growing(drv, device_id="1", timeout=POLL_TIMEOUT):
    """Wait until rx count increments, return (before, after)."""
    before = _get_rx_count(drv, device_id)
    assert before is not None, f"no BLE rx count for device {device_id}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        after = _get_rx_count(drv, device_id)
        if after > before:
            return before, after
    return before, before


def _poll_rx_frozen(drv, device_id="1", window=3.0):
    """Assert rx count doesn't change over a window."""
    before = _get_rx_count(drv, device_id)
    assert before is not None, f"no BLE rx count for device {device_id}"
    time.sleep(window)
    after = _get_rx_count(drv, device_id)
    return before, after


@pytest.mark.live
class TestLiveSmoke:
    def test_status_reachable(self, live_drv):
        result = live_drv.status()
        assert result.startswith("OK (")
        assert "devices)" in result

    def test_list_devices_has_cvd(self, live_drv):
        devices = live_drv._list_devices_raw()
        assert len(devices) >= 1
        assert any(d.get("id") == 1 for d in devices)

    def test_device_has_bluetooth_chip(self, live_drv):
        devices = live_drv._list_devices_raw()
        cvd = next(d for d in devices if d.get("id") == 1)
        bt_chips = [c for c in cvd.get("chips", []) if c.get("kind") == "BLUETOOTH"]
        assert len(bt_chips) >= 1
        assert "bt" in bt_chips[0]

    def test_beacons_exist(self, live_drv):
        devices = live_drv._list_devices_raw()
        beacons = [d for d in devices if "beacon" in d.get("name", "").lower()]
        assert len(beacons) >= 2


@pytest.mark.live
class TestLiveRadioToggle:
    """set_radio off freezes BLE rx, on resumes it."""

    def test_ble_off_stops_rx(self, live_drv):
        live_drv.set_radio("1", "ble", "on")
        time.sleep(1)

        before, after = _poll_rx_growing(live_drv, "1")
        assert after > before, "rx not growing with BLE on — can't test"

        try:
            live_drv.set_radio("1", "ble", "off")
            before_off, after_off = _poll_rx_frozen(live_drv, "1", window=3.0)
            assert before_off == after_off, f"rx still growing with BLE off: {before_off} -> {after_off}"
        finally:
            live_drv.set_radio("1", "ble", "on")

        before_on, after_on = _poll_rx_growing(live_drv, "1")
        assert after_on > before_on, "rx didn't resume after BLE re-enabled"


@pytest.mark.live
class TestLiveVersionEndpoint:
    """/v1/version doesn't exist in netsim <=0.3.100."""

    def test_no_version_endpoint(self, live_drv):
        with pytest.raises(NetsimError, match="failed"):
            live_drv._request("GET", "/v1/version")


@pytest.mark.live
class TestLiveCapturePatchBroken:
    """REST PATCH for captures is broken in netsim <=0.3.100.

    This test exists to detect when a netsim upgrade fixes it, so we
    can remove the CLI fallback.
    """

    def test_rest_capture_patch_returns_error(self, live_drv):
        captures = live_drv._request("GET", "/v1/captures")
        assert isinstance(captures, dict), "unexpected captures format"
        cap_list = captures.get("captures", [])
        if not cap_list:
            pytest.skip("no captures available")  # ty: ignore[call-non-callable]
        cap_id = cap_list[0]["id"]
        resp = requests.patch(
            f"http://{live_drv.host}:{live_drv.port}/v1/captures/{cap_id}",
            json={"capture": {"state": True}},
            timeout=5,
        )
        assert not resp.ok or "Incorrect" in resp.text, (
            "REST capture PATCH succeeded - netsim may have fixed the bug. Consider removing CLI fallback."
        )


@pytest.mark.live
class TestLiveNumericIdRequired:
    """netsim REST API requires numeric device IDs in paths.

    Using a device name in the path (e.g. /v1/devices/cvd-1) returns
    "Incorrect Id type" rather than resolving. This tests that assumption
    so we know if a future version adds name-based paths.
    """

    def test_name_in_path_fails(self, live_drv):
        devices = live_drv._list_devices_raw()
        if not devices:
            pytest.skip("no devices")  # ty: ignore[call-non-callable]
        name = devices[0]["name"]
        resp = requests.patch(
            f"http://{live_drv.host}:{live_drv.port}/v1/devices/{name}",
            json={"device": {"position": {"x": 0, "y": 0, "z": 0}}},
            timeout=5,
        )
        assert not resp.ok or "Incorrect" in resp.text, (
            "Name-based device path succeeded - netsim may support name IDs now. "
            "Consider simplifying _resolve_device_id."
        )


@pytest.mark.live
class TestLiveDeviceResolution:
    def test_resolve_by_numeric_id(self, live_drv):
        assert live_drv._resolve_device_id("1") == 1

    def test_resolve_by_name(self, live_drv):
        devices = live_drv._list_devices_raw()
        if not devices:
            pytest.skip("no devices")  # ty: ignore[call-non-callable]
        name = devices[0]["name"]
        assert live_drv._resolve_device_id(name) == devices[0]["id"]

    def test_resolve_by_substring(self, live_drv):
        devices = live_drv._list_devices_raw()
        beacons = [d for d in devices if "beacon-1" in d.get("name", "")]
        if not beacons:
            pytest.skip("no beacon-1")  # ty: ignore[call-non-callable]
        assert live_drv._resolve_device_id("beacon-1") == beacons[0]["id"]
