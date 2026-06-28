//! `jumpstarter-driver-power-example --serve <uds> [--interface <fqn>]` — this crate's driver HOST.
//!
//! It registers each interface this crate implements via the `Host` builder and serves the one the
//! hub selected (the single registered driver here, or `--interface <fqn>` when a crate has several —
//! one runs per process). No per-interface macro magic; the author registers explicitly. The polyglot
//! hub spawns this binary for `type: rust:jumpstarter-driver-power-example`.

use jumpstarter_driver_power_example::{proto, MockPower, POWER_CLIENT_CLASS};

fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    jumpstarter_driver_runtime::Host::new()
        .driver(POWER_CLIENT_CLASS, proto::FILE_DESCRIPTOR_SET, || {
            proto::power_interface_server::PowerInterfaceServer::new(MockPower::default())
        })
        // .driver(..) for each additional interface this crate implements
        .run()
}
