//! `jumpstarter-driver-power-example-client <driver> <subcommand>` — the standalone CLIENT CLI
//! binary `j` spawns for a driver advertising `rust:jumpstarter-driver-power-example`.
//!
//! The entire `src/client.rs` is one line: the codegen-generated `power_client!` macro expands to a
//! `fn main` that connects `JUMPSTARTER_HOST`, resolves the driver, and dispatches to [`PowerCli`]'s
//! subcommands (`on`/`off`/`read`/`cycle`) over native gRPC — the client-side twin of the one-line
//! `power_host!` host binary. No per-crate client boilerplate; links the client SDK only.

jumpstarter_driver_power_example::power_client!(
    jumpstarter_driver_power_example::custom_client::PowerCli
);
