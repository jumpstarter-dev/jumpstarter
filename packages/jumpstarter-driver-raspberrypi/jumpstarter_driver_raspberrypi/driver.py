from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    import lgpio  # type: ignore[import-not-found]
except ImportError as err:
    raise ImportError("lgpio is not installed, lgpio might not be supported on your platform") from err

from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export


class _GPIOBase(Driver):
    """Base GPIO"""

    pin: int
    _h: int = field(init=False, repr=False)

    _CHIP_NUM: int = 0

    def __post_init__(self):
        self.pin = int(str(self.pin).lstrip("GPIO"))
        self._h = lgpio.gpiochip_open(self._CHIP_NUM)
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    def close(self):
        try:
            lgpio.gpio_free(self._h, self.pin)
            lgpio.gpiochip_close(self._h)
        except Exception:
            pass
        super().close()

    @export
    def read_pin(self) -> int:
        """Read current pin state"""
        return lgpio.gpio_read(self._h, self.pin)


@dataclass(kw_only=True)
class DigitalOutput(_GPIOBase):
    """Single GPIO output"""

    pin: int | str
    open_drain: bool = True

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()

        if self.open_drain:
            lgpio.gpio_claim_output(self._h, self.pin, 1, lgpio.SET_OPEN_DRAIN)
        else:
            lgpio.gpio_claim_output(self._h, self.pin, 0)

    @export
    def off(self) -> None:
        """Drive the pin low"""
        lgpio.gpio_write(self._h, self.pin, 0)
        self.logger.info(f"GPIO{self.pin} off() -> pin reads: {self.read_pin()}")

    @export
    def on(self) -> None:
        """Release (open-drain) or drive high"""
        lgpio.gpio_write(self._h, self.pin, 1)
        self.logger.info(f"GPIO{self.pin} on() -> pin reads: {self.read_pin()}")


@dataclass(kw_only=True)
class DigitalInput(_GPIOBase):
    """Simple GPIO input"""

    pin: int | str

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalInputClient"

    def __post_init__(self):
        super().__post_init__()
        lgpio.gpio_claim_input(self._h, self.pin)

    @export
    def wait_for_active(self, timeout: float | None = None):
        """Block until the line reads high"""

        self._wait_for(level=1, timeout=timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        """Block until the line reads low"""

        self._wait_for(level=0, timeout=timeout)

    def _wait_for(self, *, level: int, timeout: float | None):
        deadline: float | None = None if timeout is None else time.time() + timeout

        while True:
            if lgpio.gpio_read(self._h, self.pin) == level:
                return

            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(f"Timed out waiting for GPIO{self.pin} to reach level {level}")

            time.sleep(0.001)


@dataclass(kw_only=True)
class PowerSwitch(PowerInterface, DigitalOutput):
    open_drain: bool = False

    @export
    def on(self) -> None:
        """Switch on the power"""

        DigitalOutput.on(self)

    @export
    def off(self) -> None:
        """Switch off the power"""

        DigitalOutput.off(self)

    @export
    def read(self):
        return self.read_pin()
