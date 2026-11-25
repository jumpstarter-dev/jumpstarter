import pytest

from .bridge import BluetoothBridge
from .common import PROVIDED_BY
from .interface import BluetoothInterface, hci_transport, tcp_address
from .mock import MockBluetooth
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.common.utils import serve


def test_host_and_port_are_short_for_the_usual_transport():
    assert hci_transport(None, "127.0.0.1", 7300) == "tcp-client:127.0.0.1:7300"
    assert hci_transport("hci-socket:0", "127.0.0.1", 7300) == "hci-socket:0"
    assert hci_transport("serial:/dev/ttyUSB0,1000000", "ignored", 1) == "serial:/dev/ttyUSB0,1000000"


def test_only_a_tcp_transport_is_an_address_a_forward_can_reach():
    assert tcp_address("tcp-client:127.0.0.1:7300") == ("127.0.0.1", 7300)
    assert tcp_address("tcp-client:rootcanal.example.com:7301") == ("rootcanal.example.com", 7301)
    for transport in ("hci-socket:0", "usb:0", "tcp-client:7300", "tcp-client:host:port"):
        with pytest.raises(ValueError, match="not a TCP HCI port"):
            tcp_address(transport)


def test_every_driver_has_the_whole_interface_and_one_client():
    wanted = BluetoothInterface.__abstractmethods__
    assert {"start", "stop", "status", "get_bluetooth_state", "start_capture", "read_packets"} <= wanted
    for driver in (BluetoothBridge, MockBluetooth):
        assert not getattr(driver, "__abstractmethods__", None)
    assert BluetoothBridge.client() == "jumpstarter_driver_bluetooth.client.BluetoothClient"


def test_what_a_driver_can_do_is_in_its_capabilities_and_the_rest_says_what_would_do_it():
    with serve(MockBluetooth()) as client:
        state = client.get_bluetooth_state()
        assert state["capabilities"] == ["link"] == client.capabilities()
        # A mock knows nothing of a device: nothing is made up.
        assert state["adapter"] is None and state["connections"] is None and state["bonds"] is None
        assert state["link"] == client.status()
        calls = {
            "identity": client.identity,
            "enable": lambda: client.set_enabled(False),
            "bonds": client.factory_reset,
            "capture": client.start_capture,
            "packets": lambda: client.wait_for_event("connection_complete", timeout=1),
        }
        for capability, call in calls.items():
            with pytest.raises(DriverMethodNotImplemented) as refused:
                call()
            assert f"'{capability}' is not supported by this driver (MockBluetooth)" in str(refused.value)
            assert PROVIDED_BY[capability] in str(refused.value)
        with pytest.raises(DriverMethodNotImplemented, match="HCI snoop log"):
            client.read_capture()
        with pytest.raises(DriverMethodNotImplemented, match="'packets' is not supported"):
            client.should_not_have_event("disconnection_complete")
