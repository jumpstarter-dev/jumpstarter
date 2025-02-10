from dataclasses import dataclass, field

from gpiozero import DigitalInputDevice, DigitalOutputDevice, InputDevice

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class DigitalOutput(Driver):
    pin: int | str
    device: InputDevice = field(init=False)  # Start as input

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalOutputClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        # Initialize as InputDevice first
        self.device = InputDevice(pin=self.pin)

    def close(self):
        if hasattr(self, "device"):
            self.device.close()
        super().close()

    @export
    def off(self) -> None:
        if not isinstance(self.device, DigitalOutputDevice):
            self.device.close()
            self.device = DigitalOutputDevice(pin=self.pin, initial_value=None)
        self.device.off()

    @export
    def on(self) -> None:
        if not isinstance(self.device, DigitalOutputDevice):
            self.device.close()
            self.device = DigitalOutputDevice(pin=self.pin, initial_value=None)
        self.device.on()


@dataclass(kw_only=True)
class DigitalInput(Driver):
    pin: int | str
    device: DigitalInputDevice = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalInputClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.device = DigitalInputDevice(pin=self.pin)

    @export
    def wait_for_active(self, timeout: float | None = None):
        self.device.wait_for_active(timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        self.device.wait_for_inactive(timeout)
