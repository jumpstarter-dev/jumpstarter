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
use jumpstarter_core::NativeDriverBackend;
use jumpstarter_exporter::backend::DriverBackend;
use jumpstarter_exporter::control::StatusSnapshot;
use jumpstarter_exporter::logbuf::HookLog;
use jumpstarter_exporter::session::{self, RoutingTable, SharedSession};
use tokio::sync::watch;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let uds = parse_serve(std::env::args().skip(1).collect())
        .ok_or("usage: jmp-rust-host --serve <uds>  (single-entry config on stdin)")?;

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
    let routing = RoutingTable::build(backend).await?;

    // Pin the session watch channels — one fixed driver tree for the host's lifetime.
    let (_rtx, routing_rx) = watch::channel(Some(Arc::new(routing)));
    let (_stx, status_rx) = watch::channel(StatusSnapshot::default());
    let (_etx, end_rx) = watch::channel(None);
    let shared = SharedSession::new(routing_rx, status_rx, end_rx, HookLog::new());

    let hook_uds = format!("{uds}.hook");
    let server = session::serve(shared, Path::new(&uds), Path::new(&hook_uds))?;

    // Serve until the hub SIGKILLs us at lease end (or a signal terminates the process).
    let _ = server.await;
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
