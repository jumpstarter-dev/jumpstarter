# Jumpstarter process primitives

`jumpstarter-proc` provides owned Rust primitives for process containment and
scoped terminal modes. Public APIs define resource lifetimes and error contracts;
private platform backends implement the operating-system operations. The crate
has no Python, PyO3, IPC, or asynchronous scheduling dependencies.

The two public modules have separate responsibilities:

- [`process`](src/process.rs) owns a child process containment scope. Callers
  retain spawning, the startup gate, graceful shutdown, deadlines,
  assignment-failure cleanup, and reaping.
- [`console`](src/console.rs) scopes terminal output-mode changes and restoration.
  Callers retain I/O, encoding, input handling, and coordination of shared
  terminal state.

These primitives can be consumed directly by Rust runtimes. The separate
`jumpstarter-core-py` crate adapts them to `jumpstarter_core.process` and
`jumpstarter_core.console` without making Python part of their implementation.

See the [backend availability and extension contract](src/platform/mod.rs) and
[Windows backend documentation](src/platform/windows/mod.rs) for implemented
targets, guarantees, and limits.

New backends belong behind [`src/platform/mod.rs`](src/platform/mod.rs), keeping
OS handles and implementation details out of public facades. Export a backend
only after its ownership/error contracts and platform-specific tests are in
place; adding a platform directory alone does not establish support.
