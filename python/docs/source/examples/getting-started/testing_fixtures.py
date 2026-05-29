import pytest
from jumpstarter_driver_network.adapters import PexpectAdapter
from jumpstarter_testing.pytest import JumpstarterTest


class TestBoot(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def console(self, client):
        with PexpectAdapter(client=client.dutlink.console) as console:
            yield console

    @pytest.fixture()
    def powered_device(self, client, console):
        client.dutlink.power.off()
        client.dutlink.storage.write_local_file("firmware.img")
        client.dutlink.storage.dut()
        client.dutlink.power.on()
        yield console
        client.dutlink.power.off()

    def test_device_boots(self, powered_device):
        powered_device.expect("login:", timeout=240)
