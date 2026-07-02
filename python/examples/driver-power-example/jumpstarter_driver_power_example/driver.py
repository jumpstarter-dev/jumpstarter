"""The example proto-first power driver — the Python sibling of the Rust
``jumpstarter-driver-power-example`` crate and the JVM ``KotlinPowerDriver``.

The committed ``interfaces/proto/.../power.proto`` is the source of truth: the
``jumpstarter_codegen`` build hook regenerates the ``PowerInterface`` base, the ``PowerReading``
dataclass, the typed ``PowerClient``, and the embedded descriptor into the gitignored
``_generated/`` package on every build. Only this impl (and the custom client) is committed —
if the proto changes, the regenerated base's signatures change and this impl breaks the build,
intentionally.

The generated base already IS a jumpstarter ``Driver``, so there is no ``Driver`` superclass and
no ``@export`` decorators — implementations of the interface's declared methods are exported
automatically. ``@driver(client=...)`` points the driver at the custom
:class:`~jumpstarter_driver_power_example.client.CyclingPowerClient` instead of the generated
default (the analog of Rust's ``#[driver(client = "...")]`` and the JVM's
``@JumpstarterDriver(client = ...)``).
"""

from collections.abc import AsyncIterator

from ._generated.power_driver import PowerInterface
from ._generated.power_models import PowerReading
from jumpstarter.driver import driver

ON_VOLTAGE = 5.0
"""The nominal on-voltage (volts) reported while powered."""
ON_CURRENT = 2.0
"""The nominal on-current (amps) reported while powered."""
READINGS = 3
"""How many readings ``read`` streams."""


@driver(client="jumpstarter_driver_power_example.client.CyclingPowerClient")
class ExamplePower(PowerInterface):
    """A mock power driver: ``on``/``off`` flip a powered flag, and ``read`` streams a few
    :class:`PowerReading`\\ s reflecting the current state (powered on → ``voltage > 0``)."""

    powered: bool = False

    async def on(self) -> None:
        self.logger.info("power on")
        self.powered = True

    async def off(self) -> None:
        self.logger.info("power off")
        self.powered = False

    async def read(self) -> AsyncIterator[PowerReading]:
        for _ in range(READINGS):
            if self.powered:
                yield PowerReading(voltage=ON_VOLTAGE, current=ON_CURRENT)
            else:
                yield PowerReading(voltage=0.0, current=0.0)
