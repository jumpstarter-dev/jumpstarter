//! `jmp-rust-host --serve <uds>` — serve ONE native Rust driver on a UDS for the polyglot hub.
//!
//! The exporter hub spawns this for a `runtime: rust` entry, exactly as it spawns
//! `python -m jumpstarter.exporter_host` for a Python entry: the single-entry config arrives on
//! stdin, the driver is looked up by the name after `rust:` in its `type:`, and the embedded core
//! serves the driver-host gRPC seam on `--serve <uds>` until the hub kills the process. No FFI and
//! no Python — a pure-native driver set spawns no Python at all.

use std::io::Read as _;
use std::path::Path;
use std::sync::Arc;

use jumpstarter_config::{DriverInstance, ExporterConfig, YamlConfig};
use jumpstarter_driver_core::NativeDriverBackend;
use jumpstarter_exporter::backend::DriverBackend;
use jumpstarter_exporter::session;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let uds = parse_serve(std::env::args().skip(1).collect())
        .ok_or("usage: jmp-rust-host --serve <uds>  (single-entry config on stdin)")?;

    // Exit if the hub dies before it can SIGKILL us (POSIX parent-death watchdog).
    jumpstarter_exporter::exit_when_orphaned();

    // The hub streams the single-entry config YAML on stdin (closed with EOF).
    let mut config_yaml = String::new();
    std::io::stdin().read_to_string(&mut config_yaml)?;
    let config = ExporterConfig::from_yaml(&config_yaml)?;

    let (name, instance) = config
        .export
        .iter()
        .next()
        .ok_or("config has no export entry")?;
    let base = match instance {
        DriverInstance::Base(b) => b,
        _ => return Err("jmp-rust-host expects a concrete (`type:`) entry".into()),
    };
    let driver_name = base
        .r#type
        .strip_prefix("rust:")
        .ok_or("entry `type` must be `rust:<driver>` for the rust runtime")?;
    let driver = jumpstarter_driver_example::make_driver(driver_name, &base.config)
        .ok_or_else(|| format!("unknown native driver: rust:{driver_name}"))?;

    let backend: Arc<dyn DriverBackend> = Arc::new(NativeDriverBackend::new(name, driver));

    // The shared host-SDK entrypoint builds the routing table, pins the session, and serves the
    // driver-host seam on the UDS until the hub kills us. (The same helper underpins the UniFFI
    // `serve_driver_host` foreign path and any per-crate `jumpstarter-driver-<crate>-host`.)
    session::serve_native_host(Path::new(&uds), backend).await?;
    Ok(())
}

/// Extract the `--serve <uds>` value from argv.
fn parse_serve(args: Vec<String>) -> Option<String> {
    let mut it = args.into_iter();
    while let Some(arg) = it.next() {
        if arg == "--serve" {
            return it.next();
        }
    }
    None
}
