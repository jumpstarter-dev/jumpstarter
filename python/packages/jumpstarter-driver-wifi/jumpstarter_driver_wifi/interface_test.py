import pytest

from .common import PROVIDED_BY
from .gateway import WifiGateway
from .interface import WifiInterface
from .medium import WifiMedium
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.common.utils import serve


def test_every_driver_has_the_whole_interface_and_one_client():
    wanted = WifiInterface.__abstractmethods__
    assert {"start", "stop", "status", "get_wifi_state", "start_capture", "read_packets", "wait_for_event"} <= wanted
    for driver in (WifiMedium, WifiGateway):
        assert not getattr(driver, "__abstractmethods__", None)
        assert driver.client() == "jumpstarter_driver_wifi.client.WifiClient"


@pytest.mark.parametrize("driver", [WifiMedium, WifiGateway])
def test_what_a_driver_can_do_is_in_its_capabilities_and_the_rest_says_what_would_do_it(driver):
    with serve(driver(autostart=False)) as client:
        state = client.get_wifi_state()
        assert state["capabilities"] == ["link"] == client.capabilities()
        # Neither asks the device's own Wi-Fi: nothing is made up.
        assert state["adapter"] is None and state["connections"] is None
        assert state["link"] == client.status() and state["link"]["running"] is False
        calls = {
            "capture": client.start_capture,
            "packets": lambda: client.wait_for_event("association", timeout=1),
        }
        for capability, call in calls.items():
            with pytest.raises(DriverMethodNotImplemented) as refused:
                call()
            assert f"'{capability}' is not supported by this driver ({driver.__name__})" in str(refused.value)
            assert PROVIDED_BY[capability] in str(refused.value)
        with pytest.raises(DriverMethodNotImplemented, match="pcapng"):
            client.read_capture()
        with pytest.raises(DriverMethodNotImplemented, match="'packets' is not supported"):
            client.should_not_have_event("deauthentication")
        with pytest.raises(DriverMethodNotImplemented, match="'capture' is not supported"):
            client.stop_capture()
