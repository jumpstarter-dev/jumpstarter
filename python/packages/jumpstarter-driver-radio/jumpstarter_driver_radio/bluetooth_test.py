import socket

import pytest

from .bluetooth import ControllerBluetooth, RootcanalBluetooth
from .common import BluetoothIdentity
from jumpstarter.common.utils import serve
from jumpstarter.driver import Port


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("driver", [RootcanalBluetooth, ControllerBluetooth])
def test_a_bluetooth_radio_declares_one_connects_port_that_is_its_own_bridge(driver):
    radio = driver()
    (port,) = radio.ports
    assert (port.name, port.direction, port.protocol) == ("bluetooth", "connects", "bt-l2cap-bridge")
    assert port.forwardable and port.listen is None and port.connect is None
    assert [report.name for report in radio.report().ports] == ["bluetooth"]
    assert radio._transport() == "tcp-client:127.0.0.1:7300"
    with pytest.raises(ValueError, match="own bridge"):
        driver(ports=[Port(name="bluetooth", direction="connects", listen="127.0.0.1:7301")])


def test_the_radio_is_any_hci_port_bumble_can_open():
    assert RootcanalBluetooth(port=7301)._transport() == "tcp-client:127.0.0.1:7301"
    assert ControllerBluetooth(transport="hci-socket:0")._transport() == "hci-socket:0"


def test_the_identity_of_the_device_is_what_the_far_half_is_told():
    radio = RootcanalBluetooth(name="Pixel", address="aa:bb:cc:00:11:22", class_of_device=0x5A020C, autostart=False)
    with serve(radio) as client:
        assert client.identity() == BluetoothIdentity(
            name="Pixel", address="AA:BB:CC:00:11:22", class_of_device=0x5A020C
        )
        assert client.peer() is None
        # a test that has asked the device says what it found; what it leaves out stays
        changed = client.set_identity(name="Pixel 9")
        assert (changed.name, changed.address, changed.class_of_device) == ("Pixel 9", "AA:BB:CC:00:11:22", 0x5A020C)
        assert client.set_identity(address="AA:BB:CC:00:11:33", class_of_device=0).class_of_device == 0
        assert client.identity().address == "AA:BB:CC:00:11:33"
        with pytest.raises(Exception, match="not a Bluetooth address"):
            client.set_identity(address="nonsense")


def test_a_radio_that_is_not_there_is_looked_for_and_said_so():
    with serve(RootcanalBluetooth(port=closed_port(), retry=0.05, autostart=False)) as client:
        status = client.status()
        assert status["running"] is False and status["attached"] is False and status["peer"] is False
        client.start()
        client.start()  # (twice does nothing)
        with pytest.raises(TimeoutError, match="HCI port is not there"):
            client.wait_until_attached(timeout=1.5)
        status = client.status()
        assert status["running"] and not status["attached"]
        assert client.ports() == [
            {
                "name": "bluetooth", "direction": "connects", "protocol": "bt-l2cap-bridge",
                "attachment": "socket", "transport": "stream",
            }
        ]  # fmt: skip
        client.stop()
        assert client.status()["running"] is False
        client.stop()  # (twice does nothing)


def test_the_half_starts_with_the_session_and_stops_with_it():
    radio = RootcanalBluetooth(port=closed_port(), retry=0.05)
    with serve(radio) as client:
        assert client.status()["running"] is True  # nobody called start
        client.stop()  # (stop and start stay, as overrides)
        assert client.status()["running"] is False
        client.start()
        assert client.status()["running"] is True
    assert radio._half.status()["running"] is False
