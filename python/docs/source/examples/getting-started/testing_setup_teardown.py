import pytest
from jumpstarter_testing.pytest import JumpstarterTest


class TestWithFirmware(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def flashed_device(self, client):
        client.dutlink.power.off()
        client.dutlink.storage.write_local_file("firmware.img")
        client.dutlink.storage.dut()
        client.dutlink.power.on()
        yield client
        client.dutlink.power.off()

    def test_device_responds(self, flashed_device):
        flashed_device.dutlink.power.read()
