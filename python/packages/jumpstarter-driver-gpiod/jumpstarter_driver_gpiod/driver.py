from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    import gpiod
except ImportError:
    gpiod = None

from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter_driver_gpiod.client import PinState

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class _GPIOBase(Driver):
    """Base GPIO"""

    line: int
    device: str
    _chip: gpiod.Chip = field(init=False, repr=False)
    _line: gpiod.LineRequest = field(init=False, repr=False)

    def __post_init__(self):
        if gpiod is None:
            raise ImportError(
                "gpiod is not installed, gpiod might not be supported on your platform, please install python3-gpiod"
            )
        self.line = self.line
        self._chip = gpiod.Chip(self.device)
        self._line_name = self._chip.get_line_info(self.line).name
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    def close(self):
        try:
            if hasattr(self, "_line") and self._line:
                self._line.release()
            if hasattr(self, "_chip") and self._chip:
                self._chip.close()
        except Exception:
            pass
        super().close()

    @export
    def read_pin(self):
        """Read current pin state"""
        value = self._line.get_value(self.line)
        if value == gpiod.line.Value.ACTIVE:
            return PinState.ACTIVE
        else:
            return PinState.INACTIVE

    def _line_settings(self):
        drive = gpiod.line.Drive.PUSH_PULL
        bias = gpiod.line.Bias.AS_IS

        if self.drive == "open_drain":
            drive = gpiod.line.Drive.OPEN_DRAIN
        elif self.drive in ["push_pull", None]:
            drive = gpiod.line.Drive.PUSH_PULL
        elif self.drive == "open_source":
            drive = gpiod.line.Drive.OPEN_SOURCE
        else:
            raise ValueError(f"Invalid drive: {self.drive}, must be one of: open_drain, push_pull, open_source")

        if self.bias in [None, "as_is"]:
            bias = gpiod.line.Bias.AS_IS
        elif self.bias == "pull_up":
            bias = gpiod.line.Bias.PULL_UP
        elif self.bias == "pull_down":
            bias = gpiod.line.Bias.PULL_DOWN
        elif self.bias == "disabled":
            bias = gpiod.line.Bias.DISABLED
        else:
            raise ValueError(f"Invalid bias: {self.bias}, must be one of: as_is, pull_up, pull_down, disabled")

        return gpiod.LineSettings(
            drive=drive,
            bias=bias,
            active_low=self.active_low,
        )


@dataclass(kw_only=True)
class DigitalOutput(_GPIOBase):
    """Single GPIO output"""

    device: str = field(default="/dev/gpiochip0")
    line: int
    drive: str | None = field(default=None)
    active_low: bool = field(default=False)
    bias: str | None = field(default=None)
    initial_value: str | bool = field(default="inactive")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gpiod.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()

        # Configure line settings for output
        settings = self._output_line_settings()

        self.logger.debug(f"line {self.line} ({self._line_name}) settings: {settings}")

        # Request the line
        self._line = self._chip.request_lines(config={self.line: settings}, consumer="jumpstarter-gpiod")

    def _output_line_settings(self):
        settings = self._line_settings()
        settings.direction = gpiod.line.Direction.OUTPUT

        if self.drive == "open_drain":
            settings.drive = gpiod.line.Drive.OPEN_DRAIN
        elif self.drive in ["push_pull", None]:
            settings.drive = gpiod.line.Drive.PUSH_PULL
        elif self.drive == "open_source":
            settings.drive = gpiod.line.Drive.OPEN_SOURCE
        else:
            raise ValueError(f"Invalid drive: {self.drive}, must be one of: " + "open_drain, push_pull, open_source")

        if self.initial_value in ["active", "on", True]:
            settings.output_value = gpiod.line.Value.ACTIVE
        elif self.initial_value in ["inactive", "off", False, None]:
            settings.output_value = gpiod.line.Value.INACTIVE
        else:
            raise ValueError(
                f"Invalid initial_value: {self.initial_value}, must be one of: "
                + "inactive, active, on, off, True, False"
            )

        return settings

    @export
    def off(self) -> None:
        """Set the pin to inactive state"""
        self._line.set_value(self.line, gpiod.line.Value.INACTIVE)
        self.logger.info(f"line {self.line} ({self._line_name}) off() -> pin reads: {self.read_pin()}")

    @export
    def on(self) -> None:
        """Set the pin to active state"""
        self._line.set_value(self.line, gpiod.line.Value.ACTIVE)
        self.logger.info(f"line {self.line} ({self._line_name}) on() -> pin reads: {self.read_pin()}")


@dataclass(kw_only=True)
class DigitalInput(_GPIOBase):
    """Simple GPIO input"""

    device: str = field(default="/dev/gpiochip0")
    line: int
    drive: str | None = field(default=None)
    active_low: bool = field(default=False)
    bias: str | None = field(default=None)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gpiod.client.DigitalInputClient"

    def __post_init__(self):
        super().__post_init__()

        # Configure line settings for input with edge detection
        settings = self._input_line_settings()

        self.logger.debug(f"line {self.line} ({self._line_name}) settings: {settings}")

        # Request the line
        self._line = self._chip.request_lines(config={self.line: settings}, consumer="jumpstarter-gpiod")

    def _input_line_settings(self):
        settings = self._line_settings()
        settings.direction = gpiod.line.Direction.INPUT
        settings.edge_detection = gpiod.line.Edge.BOTH
        return settings

    @export
    def wait_for_active(self, timeout: float | None = None):
        """Block until the line reads high (rising edge)"""
        if self.read_pin() == PinState.ACTIVE:
            return
        self._wait_for_edge(gpiod.EdgeEvent.Type.RISING_EDGE, timeout)

    @export
    def wait_for_edge(self, edge_type: str, timeout: float | None = None):
        """Block until the line reads high (rising edge)"""
        edge = None
        if edge_type == "rising":
            edge = gpiod.EdgeEvent.Type.RISING_EDGE
        elif edge_type == "falling":
            edge = gpiod.EdgeEvent.Type.FALLING_EDGE
        else:
            raise ValueError(f"Invalid edge type: {edge_type}, must be one of: " + "rising, falling")
        self._wait_for_edge(edge, timeout)

    @export
    def wait_for_inactive(self, timeout: float | None = None):
        """Block until the line reads low (falling edge)"""
        if self.read_pin() == PinState.INACTIVE:
            return
        self._wait_for_edge(gpiod.EdgeEvent.Type.FALLING_EDGE, timeout)

    def _wait_for_edge(self, edge_type: gpiod.EdgeEvent.Type, timeout: float | None):
        """Wait for a specific edge event using non-blocking edge detection"""
        deadline = time.time() + (timeout or 1e9)
        while True:
            remaining = deadline - time.time()
            if remaining <= 0 or not self._line.wait_edge_events(remaining):
                raise TimeoutError(f"Timed out waiting for line {self.line} edge event")

            # Read the edge events
            events = self._line.read_edge_events()

            # Check if any of the events match our target edge type
            for event in events:
                if event.line_offset == self.line and event.event_type == edge_type:
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
