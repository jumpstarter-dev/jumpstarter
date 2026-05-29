from jumpstarter_testing.pytest import JumpstarterTest


class TestPowerCycle(JumpstarterTest):
    selector = "board=rpi4"

    def test_power_on(self, client):
        client.dutlink.power.on()

    def test_power_off(self, client):
        client.dutlink.power.off()
