from dataclasses import dataclass, field

from gpiozero import DigitalInputDevice, DigitalOutputDevice

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class DigitalOutput(Driver):
    pin: int | str
    device: DigitalOutputDevice = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()
        self.device = DigitalOutputDevice(pin=self.pin)

    @export
    def off(self):
        self.device.off()

    @export
    def on(self):
        self.device.on()


@dataclass(kw_only=True)
class DigitalInput(Driver):
    pin: int | str
    device: DigitalInputDevice = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalInputClient"

    def __post_init__(self):
        super().__post_init__()
        self.device = DigitalInputDevice(pin=self.pin)

    @export
    def wait_for_active(self, timeout: float | None = None):
        self.device.wait_for_active(timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        self.device.wait_for_inactive(timeout)
