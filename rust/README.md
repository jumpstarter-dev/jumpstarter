# Jumpstarter Rust core (`jumpstarter-core`)

An idiomatic-Rust rewrite of the Jumpstarter client/exporter runtime, grown
**incrementally** alongside the existing Python implementation. The behavioral
contract this code must honor is specified in [`../specs/rust-core/`](../specs/rust-core/);
the **current Python/Go source is the source of truth** and every compatibility
claim there cites `path:line`.

This is a sibling language root to [`../python/`](../python) and
[`../controller/`](../controller). The Go controller and router are **not** being
rewritten — the Rust core is a wire-compatible peer of them.

## Migration strategy: keep the suites green at every step

The whole point of starting here is that we can opt into Rust **module by module**
behind stable abstraction boundaries, with two existing test suites as the
regression oracle (spec §5.2, "differential testing"):

1. **Go + Ginkgo e2e suite** (`../e2e/test`) — drives the real `jmp` CLI against a
   live controller/router in a kind cluster. This is the end-to-end safety net.
2. **Python pytest suites** (`../python/packages/*`) — in-process unit/integration
   tests of the runtime (e.g. `jumpstarter` core: 519 tests).

As each Rust module replaces a Python one, **both suites must stay green**. In the
interim, Python↔Rust interop and the existing gRPC boundaries keep functionality
working: the first seam is the `JUMPSTARTER_HOST` transport contract (a Rust
`jmp shell`/transport can serve the same local socket the Python `j` and driver
clients already talk to — spec §3.4), so driver-client code needs zero changes.

Phasing (spec §5.1): **A** `jmp`/`j` client CLI → **B** exporter core with a Python
driver host → **C** stream/resource data plane → **D** JEP alignment.

## Crate decomposition (spec §3.5)

Crate boundaries follow the **spec documents**, not the Python package layout.
Generated protobuf code is quarantined in exactly one crate.

| Crate | Status | Responsibility (spec doc) |
| --- | --- | --- |
| `jumpstarter-protocol` | **built** | generated prost/tonic + the `Value` codec (02/06) |
| `jumpstarter-config` | **built** | config-file / env / path-resolution models (07) |
| `jumpstarter-streams` | planned | router framing + resource encodings data plane (06) |
| `jumpstarter-client` | **built** | client runtime (04): lease lifecycle + Dial + router→UDS transport host + `jmp shell` orchestration |
| `jumpstarter-exporter` | **partial** | exporter runtime (03): register + Status/Listen + router bridge + Python driver-host; hooks/supervisor/FSM next |
| `jumpstarter-driver-host` | planned | driver-shim boundary; Python subprocess host (05) |
| `jumpstarter-cli` | **partial** | `jmp` bin (08) — `jmp shell` works; more commands + `j` next |
| `jumpstarter-py` | planned | PyO3 cdylib for Python interop |
| `jumpstarter-ffi` | planned | C-ABI cdylib for other languages |

Only crates that build are listed as workspace members in `Cargo.toml`; the rest
are added as they land.

## `jumpstarter-protocol`

Compiles the four wire services from `.proto` into prost/tonic bindings:

- `jumpstarter.v1` — `ControllerService`, `ExporterService`, `RouterService`
- `jumpstarter.client.v1` — `ClientService`

### Proto source of truth (open question 11)

The build compiles the **in-repo** [`../protocol/proto/`](../protocol/proto) tree.
Note that the Python and Go generators instead pull from the *external*
`jumpstarter-protocol` git repo (`python/buf.gen.yaml`, `controller/buf.gen.yaml`).
Which tree is authoritative is **spec open question 11**; we use the in-repo tree so
the Rust build is self-contained and matches the spec's `path:line` citations. If
the external repo is declared authoritative, only `jumpstarter-protocol/build.rs`
changes.

The third-party `google/api/*` protos (REST/AIP annotations referenced by
`client.proto`) are vendored under `jumpstarter-protocol/proto/vendor/` at the exact
`protocol/buf.lock` commit. The `google/protobuf/*` well-known types come from the
system `protoc` and map to `prost-types`.

## Building

Requires a recent stable Rust toolchain and `protoc` on `PATH` (the build uses
`tonic-build`/`prost-build`, which invoke the system protobuf compiler).

```sh
cd rust
cargo build
```
