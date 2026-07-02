"""Proto-first mock power driver — the pilot for the GENERATED authoring surface.

Unlike :class:`~jumpstarter_driver_power.driver.MockPower` (which hand-writes its interface ABC
and the proto is derived FROM it), this driver implements the interface base **generated from the
committed** ``interfaces/proto/.../power.proto`` (the source of truth) by the ``hatch_build.py``
codegen hook. Only this impl is committed; the base, the ``PowerReading`` dataclass, the typed
``PowerClient``, and the embedded descriptor are build artifacts in the gitignored ``_generated/``
package. If the proto changes, regeneration changes the base's signatures and this impl breaks —
intentionally.

Its client label (from the generated base's ``client()``) points at the generated typed client, so
``serve(NativeMockPower())`` yields a ``PowerClient`` whose calls travel the native proto-bytes
seam end to end.
"""

from collections.abc import AsyncIterator

from ._generated.power_driver import PowerInterface
from ._generated.power_models import PowerReading
from jumpstarter.driver import Driver, export


class NativeMockPower(PowerInterface, Driver):
    """A mock power driver implementing the generated proto-first ``PowerInterface``."""

    powered: bool = False

    @export
    async def on(self) -> None:
        self.logger.info("power on")
        self.powered = True

    @export
    async def off(self) -> None:
        self.logger.info("power off")
        self.powered = False

    @export
    async def read(self) -> AsyncIterator[PowerReading]:
        voltage = 5.0 if self.powered else 0.0
        yield PowerReading(voltage=voltage, current=1.0 if self.powered else 0.0)
        yield PowerReading(voltage=voltage, current=2.0 if self.powered else 0.0)
