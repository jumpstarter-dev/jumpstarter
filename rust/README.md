# Jumpstarter Rust workspace

This workspace contains reusable Rust components for Jumpstarter's runtime
and supporting tools. Domain crates define resource ownership and operations;
language bindings and runtime adapters consume those APIs.

| Crate | Public boundary | Responsibility |
| --- | --- | --- |
| [`jumpstarter-ipc`](jumpstarter-ipc/README.md) | `jumpstarter_ipc::local` | Owned local sockets and private directories. |
| [`jumpstarter-proc`](jumpstarter-proc/README.md) | `jumpstarter_proc::process`, `jumpstarter_proc::console` | Child-process containment and scoped terminal output modes. |
| [`jumpstarter-core-py`](../python/native/jumpstarter-core/README.md) | Private `jumpstarter_core._core` extension | Thin PyO3 bindings for the reusable crates. |
| [`jumpstarter-exec`](jumpstarter-exec/src/lib.rs) | Command execution bridge and its protocol | Remote command execution for the sidecar pattern. |

`jumpstarter-ipc` and `jumpstarter-proc` have no Python or PyO3 dependency. Their
owned resources use `std::io::Result` and release resources on drop. The caller
owns scheduling, cancellation, process spawning, and graceful shutdown. The
crates do not implement leases, routing, or Jumpstarter RPC protocols.

## Architecture

The IPC and process crates expose domain modules and select their private
operating-system implementations at compile time:

```text
src/
  lib.rs
  <domain>.rs
  platform/mod.rs
  platform/<os>/
```

Callers use `local`, `process`, and `console` rather than importing an OS backend.
New backends must meet the public ownership and error contracts and have tests
on the new target before their APIs are enabled. Platform-specific cleanup and
permission guarantees belong in the relevant module documentation.

API availability and implementation requirements are documented per module:
[local IPC](jumpstarter-ipc/src/local.rs),
[process containment](jumpstarter-proc/src/process.rs),
[console modes](jumpstarter-proc/src/console.rs), and
[execution bridge](jumpstarter-exec/src/lib.rs). A successful workspace build
does not establish that every module is implemented for that target.

Local socket operations currently expose nonblocking progress; the Python
AnyIO adapter waits for readiness on the socket handle and supplies cancellation. A future Tokio/tonic adapter
can consume the Rust primitives directly. That adapter and a full Rust runtime
port are not implemented here.

## Python packaging and checks

The Python distribution is `jumpstarter-core`, with the single private
`jumpstarter_core._core` extension and public scoped `local`, `process`, and
`console` modules. Maturin follows the binding crate's Cargo path dependencies
and includes both reusable crates in the source distribution. See the
[binding package](../python/native/jumpstarter-core/README.md) for its APIs,
packaging, and links to target-specific build requirements.

From this directory, `make fmt`, `make lint`, and `make test` run the Rust
workspace checks. Without Make, use `cargo fmt --all -- --check`,
`cargo clippy --locked --workspace --all-targets -- -D warnings`, and
`cargo test --locked --workspace --all-targets` directly.

The [Rust workflow](../.github/workflows/rust-tests.yaml) defines the CI target
matrix. The [wheel workflow](../.github/workflows/core-wheels.yaml) also checks
the reusable crates independently of PyO3 and rebuilds the source distribution
outside the checkout before importing the wheel. Module and backend docs
describe their examples and validation scope.
