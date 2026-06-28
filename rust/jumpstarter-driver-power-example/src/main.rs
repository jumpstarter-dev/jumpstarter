//! `jumpstarter-driver-power-example --serve <uds> [--interface <fqn>]` — this crate's driver HOST.
//!
//! The whole `src/main.rs`: `host_main!(<this crate>)` links the lib (so its `#[driver]` registrations
//! are collected), builds the host, and serves the one the hub selected (the only one here, or
//! `--interface <fqn>` when a crate implements several).

jumpstarter_driver::host_main!(jumpstarter_driver_power_example);
