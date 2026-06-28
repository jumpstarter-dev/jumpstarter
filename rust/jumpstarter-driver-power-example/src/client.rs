//! `jumpstarter-driver-power-example-client <driver> <subcommand> [--interface <fqn>]` — this crate's
//! CLIENT CLI binary (the one `j` spawns).
//!
//! The whole `src/client.rs`: `client_main!(<this crate>)` links the lib (so its `#[client_cli]`
//! registrations are collected), builds the client, and dispatches to the one matching the driver (the
//! only one here, or `--interface <fqn>` when a crate drives several).

jumpstarter_client::client_main!(jumpstarter_driver_power_example);
