# Jumpstarter local IPC

`jumpstarter-ipc` owns local byte streams, listeners, and private socket
directories. Its public boundary is `jumpstarter_ipc::local`; operating-system
implementations stay behind a private platform module, so callers do not select
or import a backend.

The crate has no Python, PyO3, or async-runtime dependency. Callers own readiness
scheduling, timeouts, and cancellation. Python bindings are a separate consumer;
a Rust runtime can use the same resource types directly. Async adapters remain
the responsibility of those consumers.

Routing, leases, authentication protocols, and the Jumpstarter wire protocol are
outside this crate's scope. Process-tree ownership and console-mode guards
belong to [`jumpstarter-proc`](../jumpstarter-proc/README.md).

Module documentation covers the API and platform contracts:

- [`local`](src/local.rs): nonblocking I/O, buffers, resource ownership, and cleanup.
- [`platform`](src/platform/mod.rs): private backend selection and target availability.
- [Windows backend](src/platform/windows/mod.rs): socket implementation, directory
  security, pathname limits, and the standalone Rust example.

Consult the module and backend documentation for target availability; a shared
API boundary does not imply an implementation on every platform.
