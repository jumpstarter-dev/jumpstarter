//! `jumpstarter-driver-power-pure --serve <uds> [--interface <fqn>]` — this crate's driver HOST.
//!
//! The whole `src/main.rs`: `host_main!()` builds the host from the crate's `#[driver]`-registered
//! drivers and serves the one the hub selected. The `use … as _` links the lib so its registrations
//! are collected. This binary links the driver side ONLY — never any client crate.

use jumpstarter_driver_power_pure as _;

jumpstarter_driver::host_main!();
