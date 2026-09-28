from __future__ import annotations

import contextlib
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

    def close(self):  # pragma: no cover
        with contextlib.suppress(Exception):
            if hasattr(self, "_line") and self._line:
                self._line.release()
            if hasattr(self, "_chip") and self._chip:
                self._chip.close()
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
    # The level status() reports, or None for "unknown". Only ever holds a level we
    # know the line took, so every path that loses that knowledge clears it.
    _driven: gpiod.line.Value | None = field(init=False, default=None, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gpiod.client.DigitalOutputClient"

    def __post_init__(self):
        super().__post_init__()

        if self.initial_value == "preserve":
            self._line, value = self._request_preserving()
        else:
            # Configure line settings for output
            value = self._parse_initial_value()
            settings = self._output_line_settings(value)

            self.logger.debug(f"line {self.line} ({self._line_name}) settings: {settings}")

            # Request the line
            self._line = self._chip.request_lines(config={self.line: settings}, consumer="jumpstarter-gpiod")

        # The request succeeded, so the line is ours and driving ``value``.
        self._driven = value
        self._verify_driven()

    def _drive(self, value) -> None:
        """Drive the line to ``value`` and record it as the level ``status()`` reports.

        ``_driven`` is cleared first so a write that raises leaves it unknown: the
        level we last drove says nothing about hardware we just failed to talk to,
        and reporting it would be a guess.
        """
        self._driven = None
        self._line.set_value(self.line, value)
        self._driven = value
        self._verify_driven()

    def _verify_driven(self) -> None:
        """Cross-check the pad against what we drove, and give up the claim if it differs.

        A push-pull output holds both halves of the swing, so it should read back
        the level it drives. A mismatch is a real fault -- a shorted pin, a dead
        pad, or a load dragging the line past the logic threshold -- and we cannot
        honestly report the driven state through one.

        Open-drain and open-source only drive one half; the other is high-impedance,
        where the pad sits wherever the external pull puts it. A mismatch there is
        expected and says nothing, so it is logged but not treated as a fault.

        A readback that fails outright does not undo the write that preceded it, so
        it is not raised; but with nothing to confirm the write, the state is unknown.
        """
        try:
            observed = self._line.get_value(self.line)
        except Exception as e:
            self.logger.warning(f"line {self.line} ({self._line_name}) readback failed ({e}); state is now unknown")
            self._driven = None
            return
        self.logger.debug(f"line {self.line} ({self._line_name}) drove {self._driven}, pin reads {observed}")

        if self.drive not in ["push_pull", None]:
            return

        if observed != self._driven:
            self.logger.warning(
                f"line {self.line} ({self._line_name}) drove {self._driven} but pin reads {observed}; "
                "the line is not following this driver, so its state is now unknown"
            )
            self._driven = None

    def _request_preserving(self):
        """Claim the line as an output without changing the level it is at.

        A plain output request always drives ``initial_value``, so every exporter
        restart switches whatever the line controls (a relay feeding a DUT's power,
        say). Instead, request the line with its direction left as-is, read the
        logical level, and only then reconfigure it to an output at that same level.

        Bias is applied in the reconfigure, not the probe: the kernel rejects bias
        flags without an explicit direction.

        This narrows the window, it does not close it. The kernel promises nothing
        about a line once its request is released, so the level may already have
        moved by the time this probe reads it, and a line that was never driven
        reads whatever its pull or float gives. Pin the level in firmware
        (config.txt ``gpio=<n>=op,dh``) or hold it in hardware when a transition
        would matter.

        Note what this does and does not settle for ``status()``. The level we end
        up driving is known -- it is exactly the one read back here -- so
        ``status()`` can report it. Whether that level was ever *intended*, as
        opposed to a reset default we found and adopted, is not knowable from the
        pad alone; that would take a record of what a previous run commanded.

        Returns the request and the level it now drives.
        """
        probe = gpiod.LineSettings(active_low=self.active_low)
        request = self._chip.request_lines(config={self.line: probe}, consumer="jumpstarter-gpiod")

        value = request.get_value(self.line)
        settings = self._output_line_settings(value)
        self.logger.debug(f"line {self.line} ({self._line_name}) preserving {value}, settings: {settings}")
        request.reconfigure_lines(config={self.line: settings})
        return request, value

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
        self._drive(gpiod.line.Value.INACTIVE)
        self.logger.info(f"line {self.line} ({self._line_name}) off() -> status: {self.status()}")

    @export
    def on(self) -> None:
        """Set the pin to active state"""
        self._drive(gpiod.line.Value.ACTIVE)
        self.logger.info(f"line {self.line} ({self._line_name}) on() -> status: {self.status()}")

    @export
    def status(self) -> str:
        """Return "on", "off", or "unknown": a best-effort view of the level this driver holds the line at.

        Best-effort because it is built from the configured settings and the line's
        readback, not from the load. It is the level last driven -- by ``on()``,
        ``off()``, or the initial request -- as long as nothing contradicts it, and
        "unknown" once something does: a write that failed, or, on a push-pull line,
        a pin that reads back something other than what was driven. Open-drain and
        open-source lines float for one of their levels, so their readback cannot
        contradict the driven level and is not used to.

        "unknown" is not terminal: ``on()`` or ``off()`` drives a level again and,
        if it takes, makes the state known.

        ``active_low`` is already applied, so this matches ``on()``/``off()``. It
        reports what the Pi drives, not whether a relay behind the line switched.
        """
        if self._driven is None:
            return "unknown"
        return "on" if self._driven == gpiod.line.Value.ACTIVE else "off"


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
