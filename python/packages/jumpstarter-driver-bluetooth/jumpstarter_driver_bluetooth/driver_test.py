"""One entry for a device's Bluetooth: a link and a device view behind one client."""

import pytest
import yaml

from .android_test import Device
from .bridge import BluetoothBridge
from .controller import BluetoothControllerPort, BluetoothRemoteController
from .driver import Bluetooth
from .forwards_test import HEAD_UNIT, PHONE, forward, lease, until
from .testing import Air
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.common.utils import serve
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.driver import Port

ADB = {"adb": "127.0.0.1:6520"}


def ports(driver) -> list[tuple]:
    return [(port.name, str(port.direction), port.protocol) for port in driver.ports]


def test_the_link_is_named_and_its_port_is_always_called_bluetooth():
    bridge = Bluetooth(link="bridge", transport="tcp-client:127.0.0.1:7301", name="Pixel", autostart=False)
    assert isinstance(bridge._link, BluetoothBridge) and bridge._view is None
    assert bridge._link._transport() == "tcp-client:127.0.0.1:7301" and bridge._link.name == "Pixel"
    assert ports(bridge) == [("bluetooth", "connects", "bt-l2cap-bridge")]

    offered = Bluetooth(link="controller-port", port=7302)
    assert isinstance(offered._link, BluetoothControllerPort)
    assert ports(offered) == [("bluetooth", "provides", "hci-h4")]
    assert offered.port_address(offered.ports[0]) == ("127.0.0.1", 7302)

    remote = Bluetooth(link="remote-controller", guest="127.0.0.1:7301", listen="127.0.0.1:7312", autostart=False)
    assert isinstance(remote._link, BluetoothRemoteController)
    assert ports(remote) == [("bluetooth", "requires", "hci-h4")] and remote.ports[0].listen == "127.0.0.1:7312"

    alone = Bluetooth(device=ADB)
    assert alone._link is None and alone.ports == [] and alone.capabilities()[0] == "state"
    assert Bluetooth().capabilities() == []
    # as the exporter reports them: the port is this entry's own
    assert [report.name for report in bridge.report().ports] == ["bluetooth"]
    assert [report.name for report in alone.report().ports] == []


def test_a_port_may_be_renamed_as_any_drivers_may():
    renamed = Bluetooth(
        link="bridge", ports=[Port(name="bluetooth-front", direction="connects", protocol="bt-l2cap-bridge")]
    )
    assert ports(renamed) == [("bluetooth-front", "connects", "bt-l2cap-bridge")]


def test_an_option_that_is_not_the_links_is_refused():
    with pytest.raises(ValueError, match="guest is not an option of 'link: bridge'"):
        Bluetooth(link="bridge", guest="127.0.0.1:7301")
    with pytest.raises(ValueError, match="learn_name, name is not an option of 'link: controller-port'"):
        Bluetooth(link="controller-port", name="Pixel", learn_name=True)
    with pytest.raises(ValueError, match="transport is not an option of 'link: remote-controller'"):
        Bluetooth(link="remote-controller", transport="tcp-client:127.0.0.1:7300")
    with pytest.raises(ValueError, match="transport needs a 'link'"):
        Bluetooth(transport="tcp-client:127.0.0.1:7300")
    with pytest.raises(ValueError, match="device: colour not understood"):
        Bluetooth(device={**ADB, "colour": "blue"})
    with pytest.raises(ValueError, match="'adb'"):
        Bluetooth(device={"adb_path": "/opt/adb"})


def test_what_a_link_asks_of_the_device_it_asks_as_the_device_view_does():
    device = {**ADB, "adb_path": "/opt/android-sdk/platform-tools/adb", "server_port": 5037}
    bridge = Bluetooth(link="bridge", device=device, autostart=False)
    source = bridge._link._source  # ty: ignore[unresolved-attribute]
    assert (source.serial, source.adb_path, source.server_port) == ("127.0.0.1:6520", device["adb_path"], 5037)
    assert (bridge._view.adb, bridge._view.adb_path) == ("127.0.0.1:6520", device["adb_path"])  # ty: ignore[unresolved-attribute]
    # ... unless the link is told where to ask
    told = Bluetooth(link="bridge", device=device, identity_from={"adb": "emulator-5554"}, autostart=False)
    assert told._link._source.serial == "emulator-5554"  # ty: ignore[unresolved-attribute]

    remote = Bluetooth(link="remote-controller", device=device, autostart=False)
    link = remote._link
    assert (link.adb, link.adb_path, link.adb_server_port) == ("127.0.0.1:6520", device["adb_path"], 5037)  # ty: ignore[unresolved-attribute]
    assert Bluetooth(link="controller-port", device=device)._view is not None
    # without a device view a bridge asks nobody, as before
    assert Bluetooth(link="bridge", autostart=False)._link._source is None  # ty: ignore[unresolved-attribute]


def test_an_exporter_configuration_reads_as_one_entry_for_a_device():
    config = ExporterConfigV1Alpha1.model_validate(
        yaml.safe_load(
            """
            apiVersion: jumpstarter.dev/v1alpha1
            kind: ExporterConfig
            metadata: { namespace: default, name: phone }
            endpoint: grpc.jumpstarter.example.com:443
            token: "<token>"
            export:
              bluetooth:
                type: jumpstarter_driver_bluetooth.driver.Bluetooth
                config:
                  transport: tcp-client:127.0.0.1:7300
                  link: bridge
                  device: { adb: "127.0.0.1:6520", adb_path: /opt/android-sdk/platform-tools/adb }
                  name: Cuttlefish phone
                  class_of_device: 0x5A020C
            """
        )
    )
    driver = config.export["bluetooth"].instantiate()
    assert isinstance(driver, Bluetooth) and ports(driver) == [("bluetooth", "connects", "bt-l2cap-bridge")]
    assert driver.capabilities() == ["link", "identity", "pairing", "state", "enable", "bonds", "capture", "packets"]
    assert driver.client() == "jumpstarter_driver_bluetooth.client.BluetoothClient"


def with_device(driver: Bluetooth) -> Device:
    device = Device()
    assert driver._view is not None
    driver._view._run = device.adb
    return device


def test_with_a_bridge_and_a_device_view_one_client_does_all_of_it():
    driver = Bluetooth(link="bridge", device=ADB, name="Cuttlefish phone", address="aa:bb:cc:00:11:22", autostart=False)
    driver._link._source = None  # ty: ignore  (the bridge's own asking of the device has its own tests)
    device = with_device(driver)
    with serve(driver) as client:
        # the link's part
        assert client.identity().name == "Cuttlefish phone" and client.peer() is None
        assert client.status()["state"] == "off" and client.status()["running"] is False
        client.start()
        assert client.status()["running"] is True
        client.stop()
        # the device's part: what the device itself says takes the place of what the link knew
        state = client.get_bluetooth_state()
        assert state["capabilities"] == [
            "link", "identity", "pairing", "state", "enable", "bonds", "capture", "packets",
        ]  # fmt: skip
        assert state["adapter"]["name"] == "Pixel 9" and state["adapter"]["android_user"] == 10
        assert state["bonds"][0]["name"] == "Head Unit"
        assert state["link"] == client.status()
        assert client.set_enabled(False)["adapter"]["enabled"] is False
        assert client.factory_reset()["bonds"] == []
        # a half that kept the key of its pairing is not a device that has a bond: the device is believed
        driver._link._half.bonded = lambda: True  # ty: ignore
        assert client.get_bluetooth_state()["bonds"] == []
        assert client.start_capture()["capturing"] is True
        assert client.read_capture()[:8] == b"btsnoop\x00"
        assert client.read_packets() == [] and client.stop_capture()["kept"] > 0
        # a device that does not answer: the link still says what it knows, and that is said
        device.there = False
        state = client.get_bluetooth_state()
        assert state["adapter"]["name"] == "Cuttlefish phone" and state["error"].endswith("not found")


def test_without_a_device_view_a_link_says_what_would_ask_the_device():
    with serve(Bluetooth(link="bridge", name="Pixel", autostart=False)) as client:
        state = client.get_bluetooth_state()
        assert state["capabilities"] == ["link", "identity", "pairing"]
        assert state["adapter"]["name"] == "Pixel" and state["bonds"] == []
        for call in (client.start_capture, client.factory_reset, lambda: client.set_enabled(True)):
            with pytest.raises(DriverMethodNotImplemented, match="a device view .AndroidBluetooth, or Bluetooth with"):
                call()


@pytest.mark.parametrize("link", ["controller-port", "remote-controller"])
@pytest.mark.parametrize("device", [None, ADB])
def test_the_ends_of_the_direct_path_with_and_without_a_device_view(link, device):
    driver = Bluetooth(link=link, device=device, autostart=False if link == "remote-controller" else None)
    if device:
        with_device(driver)
    with serve(driver) as client:
        status = client.status()
        assert status["peer"] is False and ("hosts" in status) == (link == "controller-port")
        state = client.get_bluetooth_state()
        assert state["capabilities"][0] == "link" and state["link"] == status
        assert client.ports()[0]["name"] == "bluetooth"
        with pytest.raises(DriverMethodNotImplemented, match="a bridge"):
            client.peer()
        if device:
            assert state["adapter"]["name"] == "Pixel 9"
            assert client.set_enabled(True)["adapter"]["enabled"] is True
        else:
            assert state["adapter"] is None and state["bonds"] is None
            with pytest.raises(DriverMethodNotImplemented, match="'packets' is not supported"):
                client.read_packets()


def test_with_only_a_device_view_nothing_is_joined():
    driver = Bluetooth(device=ADB)
    with_device(driver)
    with serve(driver) as client:
        assert client.ports() == [] and client.status()["attached"] is True and client.status()["peer"] is False
        client.start()
        client.stop()  # (nothing to start or stop)
        assert client.get_bluetooth_state()["link"] is None
        with pytest.raises(DriverMethodNotImplemented, match="a bridge"):
            client.identity()
    with serve(Bluetooth()) as client:
        assert client.status() == {"running": True, "attached": False, "peer": False}
        assert client.get_bluetooth_state() == {
            "capabilities": [], "adapter": None, "connections": None, "bonds": None, "link": None,
        }  # fmt: skip


@pytest.mark.anyio
async def test_a_forward_joins_two_entries_as_it_joins_two_bridges():
    def entry(identity, bridge_address: str) -> tuple[Bluetooth, Air]:
        driver = Bluetooth(
            link="bridge", name=identity.name, address=identity.address, class_of_device=identity.class_of_device
        )
        air = Air(identity, bridge_address)
        driver._link._half.open_radio = air.open_radio  # ty: ignore[unresolved-attribute]  (bumble's in-memory air)
        return driver, air

    phones, phones_air = entry(PHONE, "F0:F0:F0:F0:F0:AA")
    head_units, head_units_air = entry(HEAD_UNIT, "F0:F0:F0:F0:F0:BB")
    await phones_air.power_on()
    await head_units_air.power_on()
    async with lease(phones, head_units) as (one, two):
        # The link came up with the entry's session, and the forward found the entry's port.
        await forward("bt", one, two)
        halves = (phones._link._half, head_units._link._half)  # ty: ignore[unresolved-attribute]
        await until(lambda: all(half.device is not None for half in halves))
        assert phones_air.bridge_controller.local_name == "Head Unit"
        assert head_units_air.bridge_controller.local_name == "Phone"
        assert await phones.peer() == HEAD_UNIT.model_dump()
        status = await phones.status()
        assert status["attached"] and status["peer"] and status["on_air"]
        await phones.stop()
        await until(lambda: not halves[0].status()["running"])
    assert halves[1].status()["running"] is False
