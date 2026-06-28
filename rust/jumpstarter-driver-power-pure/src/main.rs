//! `jumpstarter-driver-power-pure --serve <uds> [--interface <fqn>]` — this crate's driver HOST.
//!
//! The whole `src/main.rs`: `host_main!(<this crate>)` links the lib (so its `#[driver]` registrations
//! are collected), builds the host, and serves the one the hub selected. This binary links the driver
//! side ONLY — never any client crate.

jumpstarter_driver::host_main!(jumpstarter_driver_power_pure);
