from __future__ import annotations

from dataclasses import dataclass, field

try:
    import gpiod
except ImportError as err:
    raise ImportError("gpiod is not installed, gpiod might not be supported on your platform, " +
                      "please install python3-gpiod") from err

from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class _GPIOBase(Driver):
    """Base GPIO"""

    pin: int
    _chip: gpiod.Chip = field(init=False, repr=False)
    _request: gpiod.LineRequest = field(init=False, repr=False)

    _CHIP_PATH: str = "/dev/gpiochip0"

    def __post_init__(self):
        self.pin = int(str(self.pin).lstrip("GPIO"))
        self._chip = gpiod.Chip(self._CHIP_PATH)
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    def close(self):
        try:
            if hasattr(self, '_request') and self._request:
                self._request.release()
            if hasattr(self, '_chip') and self._chip:
                self._chip.close()
        except Exception:
            pass
        super().close()

    @export
    def read_pin(self) -> int:
        """Read current pin state"""
        return int(self._request.get_value(self.pin))


@dataclass(kw_only=True)
class DigitalOutput(_GPIOBase):
    """Single GPIO output"""

    pin: int | str
    mode: str

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_raspberrypi.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()

        drive = gpiod.line.Drive.PUSH_PULL

        if self.mode == "open_drain":
            drive = gpiod.line.Drive.OPEN_DRAIN
        elif self.mode in ["push_pull", ""]:
            drive = gpiod.line.Drive.PUSH_PULL
        elif self.mode == "open_source":
            drive = gpiod.line.Drive.OPEN_SOURCE
        else:
            raise ValueError(f"Invalid mode: {self.mode}")

        # Configure line settings for output
        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT,
            drive=drive,
            output_value=gpiod.line.Value.INACTIVE
        )

        # Request the line
        self._request = self._chip.request_lines(
            config={self.pin: settings},
            consumer="jumpstarter-raspberrypi"
        )

    @export
    def off(self) -> None:
        """Drive the pin low"""
        self._request.set_value(self.pin, gpiod.line.Value.INACTIVE)
        self.logger.info(f"GPIO{self.pin} off() -> pin reads: {self.read_pin()}")

    @export
    def on(self) -> None:
        """Release (open-drain) or drive high"""
        self._request.set_value(self.pin, gpiod.line.Value.ACTIVE)
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

        # Configure line settings for input with edge detection
        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.INPUT,
            edge_detection=gpiod.line.Edge.BOTH  # Detect both rising and falling edges
        )

        # Request the line
        self._request = self._chip.request_lines(
            config={self.pin: settings},
            consumer="jumpstarter-raspberrypi"
        )

    @export
    def wait_for_active(self, timeout: float | None = None):
        """Block until the line reads high (rising edge)"""
        self._wait_for_edge(gpiod.EdgeEvent.Type.RISING_EDGE, timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        """Block until the line reads low (falling edge)"""
        self._wait_for_edge(gpiod.EdgeEvent.Type.FALLING_EDGE, timeout)

    def _wait_for_edge(self, edge_type: gpiod.EdgeEvent.Type, timeout: float | None):
        """Wait for a specific edge event using non-blocking edge detection"""

        while True:
            # Wait for edge events to become available
            if not self._request.wait_edge_events(timeout):
                raise TimeoutError(f"Timed out waiting for GPIO{self.pin} edge event")

            # Read the edge events
            events = self._request.read_edge_events()

            # Check if any of the events match our target edge type
            for event in events:
                if event.line_offset == self.pin and event.event_type == edge_type:
                    return


@dataclass(kw_only=True)
class PowerSwitch(PowerInterface, DigitalOutput):
    mode: str = "push_pull"

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
