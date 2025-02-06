from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from time import sleep

import board
from digitalio import DigitalInOut, DriveMode, Pull
from jumpstarter_driver_power.driver import PowerInterface, PowerReading

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class DigitalIO(Driver):
    pin: str
    device: DigitalInOut = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalIOClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        # Defaults to input with no pull
        try:
            self.device = DigitalInOut(pin=getattr(board, self.pin))
        except AttributeError as err:
            raise ValueError(f"Invalid pin name: {self.pin}") from err

    def close(self):
        if hasattr(self, "device"):
            self.device.deinit()

    @export
    def switch_to_output(self, value: bool = False, drive_mode: int = 0) -> None:
        match drive_mode:
            case 0:
                drive_mode = DriveMode.PUSH_PULL
            case 1:
                drive_mode = DriveMode.OPEN_DRAIN
            case _:
                raise ValueError("unrecognized drive_mode")

        self.device.switch_to_output(value, drive_mode)

    @export
    def switch_to_input(self, pull: int = 0) -> None:
        match pull:
            case 0:
                pull = None
            case 1:
                pull = Pull.UP
            case 2:
                pull = Pull.DOWN
            case _:
                raise ValueError("unrecognized pull")

        self.device.switch_to_input(pull)

    @export
    def set_value(self, value: bool) -> None:
        self.device.value = value

    @export
    def get_value(self) -> bool:
        return self.device.value


@dataclass(kw_only=True)
class DigitalPowerSwitch(PowerInterface, DigitalIO):
    value: bool = False
    drive_mode: str = "PUSH_PULL"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        try:
            self.device.switch_to_output(value=self.value, drive_mode=getattr(DriveMode, self.drive_mode))
        except AttributeError as err:
            raise ValueError(f"Invalid drive mode: {self.drive_mode}") from err

    @export
    def on(self) -> None:
        self.device.value = True

    @export
    def off(self) -> None:
        self.device.value = False

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        raise NotImplementedError


@dataclass(kw_only=True)
class DigitalPowerButton(PowerInterface, DigitalIO):
    value: bool = False
    drive_mode: str = "OPEN_DRAIN"
    on_press_seconds: int = 1
    off_press_seconds: int = 5

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        try:
            self.device.switch_to_output(value=self.value, drive_mode=getattr(DriveMode, self.drive_mode))
        except AttributeError as err:
            raise ValueError(f"Invalid drive mode: {self.drive_mode}") from err

    def press(self, seconds: int) -> None:
        self.device.value = self.value
        self.device.value = not self.value
        sleep(seconds)
        self.device.value = self.value

    @export
    def on(self) -> None:
        self.press(self.on_press_seconds)

    @export
    def off(self) -> None:
        self.press(self.off_press_seconds)

    @export
    def read(self) -> AsyncGenerator[PowerReading, None]:
        raise NotImplementedError
