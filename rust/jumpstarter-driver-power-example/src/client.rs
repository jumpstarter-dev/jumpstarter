//! `jumpstarter-driver-power-example-client <driver> <subcommand>` — the standalone CLIENT CLI
//! binary `j` spawns for a driver whose advertised client is `rust:jumpstarter-driver-power-example`.
//! The client-side mirror of the per-crate HOST binary: it connects `JUMPSTARTER_HOST`, resolves the
//! driver, and runs the typed [`PowerCli`] over native gRPC. It links the client SDK only — never the
//! `j` / `jmp` CLI — so `j` spawns it by name (PATH / dev-build) and links no client code itself.

use std::process::ExitCode;

use jumpstarter_core::ClientSession;
use jumpstarter_driver_power_example::custom_client::PowerCli;
use jumpstarter_driver_power_example::PowerClient;

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(driver) = args.first().cloned() else {
        eprintln!("usage: <driver> <subcommand> [args]");
        return ExitCode::from(2);
    };
    let host = match std::env::var("JUMPSTARTER_HOST") {
        Ok(h) => h,
        Err(_) => {
            eprintln!("JUMPSTARTER_HOST is not set (run inside a `jmp shell`)");
            return ExitCode::from(1);
        }
    };
    let session = match ClientSession::connect(host).await {
        Ok(s) => s,
        Err(e) => {
            eprintln!("connecting to the exporter: {e}");
            return ExitCode::from(1);
        }
    };
    // The typed client resolves the driver's uuid by name from the report.
    let uuid = match PowerClient::new(&session, &driver).await {
        Ok(client) => client.uuid().to_string(),
        Err(e) => {
            eprintln!("resolving driver '{driver}': {e}");
            return ExitCode::from(1);
        }
    };
    ExitCode::from(PowerCli::run(&args[1..], &session, &uuid).await as u8)
}
