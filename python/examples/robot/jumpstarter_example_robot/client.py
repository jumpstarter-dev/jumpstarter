"""Client of the mock board, showing how a driver curates its Robot Framework keywords."""

from jumpstarter_driver_composite.client import CompositeClient

from jumpstarter.client.decorators import robot


class MockDutClient(CompositeClient):
    """Mock board with power and a serial console."""

    @robot.keyword("Reboot", tags=["power"])
    def reboot(self, wait: float = 1.0) -> None:
        """Power cycle the board, waiting ``wait`` seconds while it is off."""
        self.power.cycle(wait)

    @robot.keyword("Should Be Powered", tags=["assert"])
    def should_be_powered(self) -> None:
        """Fail unless the board is powered on."""
        self.power.should_be_on()

    @robot.not_keyword
    def raw_status(self) -> str | None:
        """Python-only helper; hidden from Robot Framework."""
        return self.power.status()
