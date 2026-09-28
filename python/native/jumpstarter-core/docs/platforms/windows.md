# Windows bindings and builds

The current `jumpstarter_core.local` and `jumpstarter_core.process` APIs use
Windows backends. Their native classes are
registered only under `cfg(windows)`. Building the extension on another target
does not make these APIs available there. Existing Linux and macOS Jumpstarter
sessions continue to use their Python/AnyIO implementations.

The module docs describe their contracts and Windows behavior:

- [Local IPC](../../jumpstarter_core/local.py): AF_UNIX streams, private socket
  directories, pathname limits, and cooperative asynchronous polling.
- [Process containment](../../jumpstarter_core/process.py): a private Job Object,
  child startup ordering, and cleanup responsibilities.

The binding exposes owned objects rather than raw socket or process handles and
does not use Python `ctypes`. The reusable implementations live in the
[IPC backend](../../../../../rust/jumpstarter-ipc/src/platform/windows/mod.rs)
and [process backend](../../../../../rust/jumpstarter-proc/src/platform/windows/mod.rs).

## Building

Build the Windows x64 extension with maturin and the Rust MSVC target
`x86_64-pc-windows-msvc`. A local build needs the MSVC C++ build tools, Windows
SDK, and Python 3.12 or newer. From `python/native/jumpstarter-core`, run:

```powershell
uvx --from maturin==1.15.0 maturin build --release --locked --target x86_64-pc-windows-msvc --interpreter python
```

The resulting wheel uses the `cp312-abi3-win_amd64` compatibility tag. The stable
ABI is a build contract; supported Python versions still need runtime tests.
See the [package README](../../README.md) for source-distribution packaging and
the shared lockfile checks.

## Validation

The [wheel workflow](../../../../../.github/workflows/core-wheels.yaml) checks
both reusable crates without PyO3, runs the Rust-only IPC `local_roundtrip`
example, builds the wheel, and rebuilds the extracted source distribution
outside the checkout before importing it. These checks cover the Windows
backend and do not establish Linux or macOS Rust backend support.

The standalone IPC example can also run from the repository's `rust` directory:

```text
cargo run --locked -p jumpstarter-ipc --example local_roundtrip
```

It checks binary I/O, pending reads, partial writes, half-close, listener/stream
ownership, explicit close, and drop cleanup without loading Python.
