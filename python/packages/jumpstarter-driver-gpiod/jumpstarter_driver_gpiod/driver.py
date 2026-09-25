from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass, field

try:
    import gpiod
except ImportError:
    gpiod = None  # ty: ignore[invalid-assignment]

from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import PowerInterface

from jumpstarter_driver_gpiod.client import PinState

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class _GPIOBase(Driver):
    """Base GPIO"""

    driver_type = "gpio"

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

        if self.initial_value == "preserve":
            self._line = self._request_preserving()
            return

        # Configure line settings for output
        settings = self._output_line_settings(self._parse_initial_value())

        self.logger.debug(f"line {self.line} ({self._line_name}) settings: {settings}")

        # Request the line
        self._line = self._chip.request_lines(config={self.line: settings}, consumer="jumpstarter-gpiod")

    def _request_preserving(self):
        """Claim the line as an output without changing the level it is at.

        A plain output request always drives ``initial_value``, so every exporter
        restart switches whatever the line controls (a relay feeding a DUT's power,
        say). Instead, request the line with its direction left as-is, read the
        logical level, and only then reconfigure it to an output at that same level.

        Bias is applied in the reconfigure, not the probe: the kernel rejects bias
        flags without an explicit direction. A line that was never driven reads
        whatever its pull or float gives, so pin it in firmware (config.txt
        ``gpio=<n>=op,dh``) when its cold-boot level matters.
        """
        probe = gpiod.LineSettings(active_low=self.active_low)
        request = self._chip.request_lines(config={self.line: probe}, consumer="jumpstarter-gpiod")

        value = request.get_value(self.line)
        settings = self._output_line_settings(value)
        self.logger.debug(f"line {self.line} ({self._line_name}) preserving {value}, settings: {settings}")
        request.reconfigure_lines(config={self.line: settings})
        return request

    def _parse_initial_value(self):
        if self.initial_value in ["active", "on", True]:
            return gpiod.line.Value.ACTIVE
        if self.initial_value in ["inactive", "off", False, None]:
            return gpiod.line.Value.INACTIVE
        raise ValueError(
            f"Invalid initial_value: {self.initial_value}, must be one of: "
            + "inactive, active, on, off, preserve, True, False"
        )

    def _output_line_settings(self, output_value):
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

        settings.output_value = output_value
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

    @export
    def status(self) -> str:
        """Return "on" or "off": the logical level this driver holds the line at.

        ``active_low`` is already applied, so this matches ``on()``/``off()``. It
        reports what the Pi drives, not whether a relay behind the line switched.
        """
        return "on" if self.read_pin() == PinState.ACTIVE else "off"


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
    """A GPIO line switching a load, e.g. one channel of a relay HAT.

    Speaks PowerInterface (on/off/cycle) like the other relay drivers, adds
    ``status``, and inherits ``initial_value: preserve`` from DigitalOutput.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gpiod.client.PowerSwitchClient"

    @export
    def on(self) -> None:
        """Switch on the power"""
        DigitalOutput.on(self)

    @export
    def off(self) -> None:
        """Switch off the power"""
        DigitalOutput.off(self)

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        raise NotImplementedError
        yield  # makes this a generator function
