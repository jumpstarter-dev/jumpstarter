"""Proto-first mock power driver — the pilot for the GENERATED authoring surface.

Unlike :class:`~jumpstarter_driver_power.driver.MockPower` (which hand-writes its interface ABC
and the proto is derived FROM it), this driver implements the interface base **generated from the
committed** ``interfaces/proto/.../power.proto`` (the source of truth) by the ``jumpstarter_codegen``
build hook. Only this impl is committed; the base, the ``PowerReading`` dataclass, the typed
``PowerClient``, and the embedded descriptor are build artifacts in the gitignored ``_generated/``
package. If the proto changes, regeneration changes the base's signatures and this impl breaks —
intentionally.

The generated base IS a jumpstarter ``Driver``, so nothing else is subclassed and no ``@export``
decorators are needed — implementations of the interface's declared methods are exported
automatically (the analog of Rust's ``#[driver] impl PowerInterface`` and the JVM's
``@JumpstarterDriver`` service subclass). Its default client label points at the generated typed
client, so ``serve(NativeMockPower())`` yields a ``PowerClient`` whose calls travel the native
proto-bytes seam end to end.
"""

from collections.abc import AsyncIterator

from ._generated.power_driver import PowerInterface
from ._generated.power_models import PowerReading


class NativeMockPower(PowerInterface):
    """A mock power driver implementing the generated proto-first ``PowerInterface``."""

    powered: bool = False

    async def on(self) -> None:
        self.logger.info("power on")
        self.powered = True

    async def off(self) -> None:
        self.logger.info("power off")
        self.powered = False

    async def read(self) -> AsyncIterator[PowerReading]:
        voltage = 5.0 if self.powered else 0.0
        yield PowerReading(voltage=voltage, current=1.0 if self.powered else 0.0)
        yield PowerReading(voltage=voltage, current=2.0 if self.powered else 0.0)
