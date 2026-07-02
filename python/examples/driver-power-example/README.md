# jumpstarter-driver-power-example (Python)

The example **proto-first** Python driver — the Python sibling of the Rust
`rust/jumpstarter-driver-power-example` crate and the JVM `java/jumpstarter-driver-power-example`
module. It shows the recommended way to author a Jumpstarter driver in Python: the committed
`.proto` is the contract, everything derived from it is generated at build time, and calls travel
the native proto-bytes seam end to end.

## What is committed vs. generated

Committed (this package):

- `jumpstarter_driver_power_example/driver.py` — `ExamplePower`, implementing the **generated**
  `PowerInterface` base. The base already IS a jumpstarter `Driver`, so there is no `Driver`
  superclass and no `@export` decorators: implementations of the interface's declared methods are
  exported automatically. `@driver(client=...)` advertises the custom client (the analog of Rust's
  `#[driver(client = "...")]` and the JVM's `@JumpstarterDriver(client = ...)`).
- `jumpstarter_driver_power_example/client.py` — `CyclingPowerClient`, subclassing the
  **generated** typed `PowerClient` to add client-side conveniences (`cycle`, `read_voltages`)
  and a `j` CLI.

Generated into the gitignored `jumpstarter_driver_power_example/_generated/` on every build by
the `jumpstarter_codegen` hook (see `[tool.hatch.build.hooks.jumpstarter_codegen]` in
`pyproject.toml`), from `interfaces/proto/jumpstarter/interfaces/power/v1/power.proto`:

- `power_driver.py` — the abstract `PowerInterface` driver base (native-typed signatures)
- `power_models.py` — the `PowerReading` message as a plain stdlib dataclass
- `power_client.py` — the typed `PowerClient` driving the native proto-bytes seam
- `power_descriptor.py` — the embedded `FileDescriptorSet` (the analog of Rust's
  `FILE_DESCRIPTOR_SET` and the JVM's `DescriptorSets`)

If the proto changes, regeneration changes the base's signatures and a stale impl fails to
instantiate — the enforced "proto is the source of truth" property, matching Rust/JVM.

## Authoring surface

```python
from jumpstarter.driver import driver

from ._generated.power_driver import PowerInterface
from ._generated.power_models import PowerReading


@driver(client="jumpstarter_driver_power_example.client.CyclingPowerClient")
class ExamplePower(PowerInterface):
    powered: bool = False

    async def on(self) -> None:
        self.powered = True

    async def off(self) -> None:
        self.powered = False

    async def read(self) -> AsyncIterator[PowerReading]:
        yield PowerReading(voltage=5.0 if self.powered else 0.0, current=2.0)
```

No protobuf, grpcio, or pydantic anywhere in the authoring surface — messages are stdlib
dataclasses, and the proto marshalling lives in the generic runtime
(`jumpstarter.driver.proto_marshal`).

## Running

```console
$ cd python
$ make codegen                              # regenerate _generated/ after a proto edit
$ uv run --isolated --directory examples/driver-power-example pytest
```

The tests drive the full loop: `serve(ExamplePower())` resolves `CyclingPowerClient` from the
driver's client label, and `on`/`off` (unary) plus `read` (server-streaming) cross the native
`forward_unary`/`forward_server_stream` seam as real proto bytes.

To serve it from an exporter config, the driver is registered under the
`jumpstarter.drivers` entry point:

```yaml
export:
  power:
    type: jumpstarter_driver_power_example.driver.ExamplePower
```
