//! `jumpstarter-driver-power-pure-client <driver> <subcommand>` — this crate's CLIENT CLI binary
//! (the one `j` spawns).
//!
//! The whole `src/main.rs`: `client_main!(<this crate>)` links the lib (so its `#[client_cli]`
//! registrations are collected), builds the client, and dispatches to the one matching the driver.
//! This binary links the client side ONLY — never any driver crate.

jumpstarter_client::client_main!(jumpstarter_driver_power_pure_client);
