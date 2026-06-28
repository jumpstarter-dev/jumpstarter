//! `jumpstarter-driver-power-example-client <driver> <subcommand> [--interface <fqn>]` — this crate's
//! CLIENT CLI binary (the one `j` spawns).
//!
//! It registers each interface's CLI via the `Client` builder and dispatches to the one matching the
//! driver (the single registered CLI here, or `--interface <fqn>` when a crate drives several — one
//! runs per process). No per-interface macro magic; the author registers explicitly.

use jumpstarter_driver_power_example::{custom_client::PowerCli, proto};

fn main() -> std::process::ExitCode {
    jumpstarter_core::Client::new()
        .cli(proto::FILE_DESCRIPTOR_SET, |args, session, uuid| {
            Box::pin(PowerCli::run(args, session, uuid))
        })
        // .cli(..) for each additional interface this crate drives
        .run()
}
