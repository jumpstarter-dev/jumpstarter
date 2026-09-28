# Jumpstarter Rust core Python bindings

`jumpstarter-core` exposes Jumpstarter's reusable Rust runtime components to
Python. Scoped Python modules share a private PyO3 extension built with maturin.
Native Rust consumers use the underlying crates directly, without embedding
Python or adopting a particular asynchronous runtime.

## Components

| Layer | Responsibility |
| --- | --- |
| [`jumpstarter-ipc`](../../../rust/jumpstarter-ipc/README.md) | Owned local sockets and private directories with `std::io::Result` errors. |
| [`jumpstarter-proc`](../../../rust/jumpstarter-proc/README.md) | Child-process containment and scoped terminal modes with `std::io::Result` errors. |
| [`jumpstarter-core-py`](../../../rust/jumpstarter-core-py/src/lib.rs) | Thin PyO3 binding: converts arguments and results, maps errors, and releases the interpreter during native operations. |
| [`jumpstarter_core.local`](jumpstarter_core/local.py) | Nonblocking local IPC consumed by Jumpstarter's AnyIO adapter. The caller supplies scheduling and cancellation. |
| [`jumpstarter_core.process`](jumpstarter_core/process.py) | Ownership and cleanup of a child-process tree. The caller supplies spawning, graceful shutdown, deadlines, and reaping. |
| [`jumpstarter_core.console`](jumpstarter_core/console.py) | Scoped terminal output modes. The caller supplies input, output, and encoding. |

The reusable crates have no Python or PyO3 dependencies. Their public domain
modules delegate OS operations to private platform implementations. Python
modules document their current availability, ownership rules, and usage; a
successful package build does not imply that every component supports that
target. See the [Rust workspace architecture](../../../rust/README.md).

## Packaging

The distribution contains one private `jumpstarter_core._core` extension and
public scoped Python APIs. Future components can join the same extension
through their own modules. The extension uses CPython's stable ABI from Python
3.12; each supported Python version still requires runtime qualification.

Release builds give the distribution the same version as the other Jumpstarter
packages, which pin it exactly on Windows. The checked-in `0.0.0` is a
placeholder for local builds; `python/scripts/set_core_version.py` stamps the
version computed by `hatch-vcs`. The [wheel workflow](../../../.github/workflows/core-wheels.yaml)
builds x64 and ARM64 wheels natively on each architecture, tests them, and
attaches them and the source distribution to published GitHub releases.

The package lives outside the main Python workspace so consumers select its
native build requirements explicitly. Maturin includes the Rust sources and
workspace metadata in the source distribution, including both `jumpstarter-ipc`
and `jumpstarter-proc` path dependencies. CI rebuilds that distribution outside
the checkout and imports the resulting wheel.

Maturin reduces workspace membership in the source archive but retains the full
workspace lockfile. Before rebuilding the archive, CI lets Cargo prune unused
entries offline and verifies that retained packages, versions, sources,
checksums, and resolved dependencies are unchanged. The wheel rebuild then uses
`--locked --offline`; the repository lockfile and published archive are unchanged.

See the [platform build notes](docs/platforms/windows.md) for the currently
implemented target, toolchain requirements, and validation scope.

## Rust runtime integration

Additional Rust components should extend this distribution through scoped
Python modules and the shared PyO3/maturin binding layer. Keep ownership of the
`jumpstarter_core` namespace in one distribution.

A Rust client or exporter can consume `jumpstarter_ipc::local`,
`jumpstarter_proc::process`, and `jumpstarter_proc::console` directly. An
asynchronous adapter belongs above these primitives: the Tokio/tonic stream
adapter and a full Rust runtime port are not implemented here.
