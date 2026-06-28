//! `jumpstarter-driver-power-pure-client <driver> <subcommand>` — this crate's CLIENT CLI binary
//! (the one `j` spawns).
//!
//! The whole `src/main.rs`: `client_main!()` builds the client from the crate's `#[client_cli]`-
//! registered CLIs and dispatches to the one matching the driver. The `use … as _` links the lib so
//! its registrations are collected. This binary links the client side ONLY — never any driver crate.

use jumpstarter_driver_power_pure_client as _;

jumpstarter_client::client_main!();
