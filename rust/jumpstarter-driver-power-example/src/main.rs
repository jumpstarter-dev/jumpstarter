//! `jumpstarter-driver-power-example --serve <uds> [--interface <fqn>]` — this crate's driver HOST.
//!
//! The whole `src/main.rs`: `host_main!()` builds the host from the crate's `#[driver]`-registered
//! drivers and serves the one the hub selected (the only one here, or `--interface <fqn>` when a crate
//! implements several). The `use … as _` links the lib so its registrations are collected.

use jumpstarter_driver_power_example as _;

jumpstarter_driver::host_main!();
