from dataclasses import InitVar, dataclass, field

from gpiozero import DigitalInputDevice, DigitalOutputDevice

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class DigitalOutput(Driver):
    pin: InitVar[int | str]
    device: DigitalOutputDevice = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.rpi.client.DigitalOutputClient"

    def __post_init__(self, name, pin):
        super().__post_init__(name)
        self.device = DigitalOutputDevice(pin=pin)

    @export
    def off(self):
        self.device.off()

    @export
    def on(self):
        self.device.on()


@dataclass(kw_only=True)
class DigitalInput(Driver):
    pin: InitVar[int | str]
    device: DigitalInputDevice = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.rpi.client.DigitalInputClient"

    def __post_init__(self, name, pin):
        super().__post_init__(name)
        self.device = DigitalInputDevice(pin=pin)

    @export
    def wait_for_active(self, timeout: float | None = None):
        self.device.wait_for_active(timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        self.device.wait_for_inactive(timeout)
