//! `jumpstarter-driver-power-example-client <driver> <subcommand> [--interface <fqn>]` — this crate's
//! CLIENT CLI binary (the one `j` spawns).
//!
//! The whole `src/client.rs`: `client_main!()` builds the client from the crate's `#[client]`-registered
//! CLIs and dispatches to the one matching the driver (the only one here, or `--interface <fqn>` when a
//! crate drives several). The `use … as _` links the lib so its registrations are collected.

use jumpstarter_driver_power_example as _;

jumpstarter_core::client_main!();
