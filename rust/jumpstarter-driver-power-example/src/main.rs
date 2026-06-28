//! `jumpstarter-driver-power-example --serve <uds>` — this crate IS its own standalone driver host.
//!
//! The entire `src/main.rs` is one line: the codegen-generated `power_host!` macro bakes in the
//! client class, descriptor, and `tonic` server type (so only the driver instance is named) and
//! expands to a `fn main` that parses `--serve`, installs the parent-death watchdog, reads the hub's
//! stdin config, and serves the driver-host seam until killed. No per-crate host boilerplate; it
//! links the host SDK only — never the `jmp` CLI. The polyglot hub spawns this crate's own binary
//! for a `type: rust:jumpstarter-driver-power-example` entry.

jumpstarter_driver_power_example::power_host!(jumpstarter_driver_power_example::MockPower::default());
