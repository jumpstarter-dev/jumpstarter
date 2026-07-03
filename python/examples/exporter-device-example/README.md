# jumpstarter-exporter-device-example (Python)

The example **typed root client**: from the committed [`exporter.yaml`](exporter.yaml), the
`jumpstarter_codegen` build hook generates a class that IS the exporter's root driver — the
config's single `dut` entry — with its children as typed attributes: `rig.power`,
`rig.backup_power`. This is what a test project consuming a Jumpstarter exporter looks like: **no
hand-written clients required**, full autocomplete, and resolution that never loads driver code
at build time (proto-only, via the committed `interfaces/registry/` + this package's
[`registry.yaml`](registry.yaml)).

## What is committed vs. generated

Committed: `exporter.yaml` (the source of truth), `registry.yaml` (the out-of-tree-driver
pattern: maps this package's driver `type:`s to their interfaces and custom clients), and the
test. Generated into the gitignored `_generated/` on every build:

- `device.py` — `ExampleRig(root)`: the typed `dut` root client itself
- `power_client.py` / `power_models.py` / `power_descriptor.py` — the typed per-interface client

## How each node binds

| node | resolution | binding |
|---|---|---|
| `rig.power` | explicit `interface:` key | `CyclingPowerClient` (custom, from `registry.yaml`) |
| `rig.backup_power` | committed `interfaces/registry/` | generated `PowerClient` (no custom client advertised) |

A node's runtime client label is irrelevant to the wrapper: each node is **rebound** to the
codegen-chosen class (`rebind_client`), so the tree works even when a node's driver package
isn't installed client-side (`StubDriverClient` upgrades to the generated client).

## Running

```console
$ cd python
$ make codegen        # regenerate _generated/ after editing exporter.yaml
$ uv run --isolated --directory examples/exporter-device-example pytest
```

The test serves the SAME `exporter.yaml` in-process (`DriverHostFactory.from_yaml` +
`LocalSession`), so the generated wrapper and the served tree cannot drift.
