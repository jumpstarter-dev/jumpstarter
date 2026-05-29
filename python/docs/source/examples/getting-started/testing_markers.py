import pytest
from jumpstarter_testing.pytest import JumpstarterTest


class TestOptionalFeatures(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.mark.slow
    def test_power_cycle(self, client):
        client.dutlink.power.on()
        client.dutlink.power.cycle(wait=5)
        client.dutlink.power.off()

    @pytest.mark.skip(reason="hardware not available")
    def test_camera_capture(self, client):
        image = client.camera.snapshot()
        image.save("capture.jpeg")
