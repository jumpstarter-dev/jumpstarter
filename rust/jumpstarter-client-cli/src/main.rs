//! Native `j` — the client-side driver CLI, the client-side mirror of the polyglot exporter hub.
//!
//! `j <driver> <cmd> <args>` drives a leased exporter's drivers. Native `j` connects via
//! `ClientSession`, reads the report, and **routes each driver to its client language** (from the
//! `jumpstarter.dev/client` label): a native Rust client runs **in-process** (no Python), while a
//! Python client class is delegated to the Python driver-client CLI (`python -m jumpstarter_cli.j`),
//! preserving its rich click CLI verbatim. A pure-native-client set needs no Python at all.

use std::process::Command;

use jumpstarter_client::ClientSession;
use serde_json::Value as Json;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    // Wire tracing/RUST_LOG consistently with the `jmp` binary; without this the native `j`
    // entrypoint emitted no logs even with RUST_LOG set.
    init_tracing();
    let args: Vec<String> = std::env::args().skip(1).collect();

    // Non-driver invocations (no args, a global flag, the `introspect` MCP side channel) go to the
    // Python driver-client CLI, which owns the top-level help + introspection.
    let first = args.first().map(String::as_str).unwrap_or("");
    tracing::debug!(driver = first, args = ?args, "j dispatch entry");
    if first.is_empty() || first.starts_with('-') || first == "introspect" {
        tracing::debug!(
            driver = first,
            "delegating non-driver invocation to python driver-client CLI"
        );
        delegate_to_python(&args);
    }

    // A driver invocation: connect to route it to the owning client's language. Without a lease
    // (no JUMPSTARTER_HOST), let the Python CLI emit its "use inside a jmp shell" message.
    let host = match std::env::var("JUMPSTARTER_HOST") {
        Ok(h) => h,
        Err(_) => {
            tracing::debug!("JUMPSTARTER_HOST unset; delegating to python driver-client CLI");
            delegate_to_python(&args)
        }
    };
    tracing::debug!(driver = first, "connecting ClientSession to route driver");
    let session = match ClientSession::connect(host).await {
        Ok(s) => s,
        Err(e) => {
            tracing::error!(error = %e, "connecting to exporter failed");
            fail(&format!("connecting to exporter: {e}"))
        }
    };
    let report = match session.get_report().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!(error = %e, "fetching driver report failed");
            fail(&format!("fetching driver report: {e}"))
        }
    };

    match client_for(&report, first) {
        // A native (Rust) client → spawn the crate's standalone client CLI binary (the client-side
        // mirror of the per-crate host), exactly as a JVM/Python client delegates to its launcher.
        // `j` links no client code; it dispatches by the advertised label.
        Some((_uuid, label)) if label.starts_with("rust:") => {
            tracing::debug!(driver = first, %label, "routing to rust client cli binary");
            drop(session);
            delegate_to_rust_client(&label, &args);
        }
        // A JVM (Java/Kotlin) client → the JVM client CLI (picocli, `dev.jumpstarter.cli.JMain`),
        // exactly as a Python client delegates to the Python CLI. It re-reads JUMPSTARTER_HOST.
        Some((_uuid, label)) if label.starts_with("jvm:") => {
            tracing::debug!(driver = first, %label, "routing to jvm client cli");
            drop(session);
            delegate_to_jvm(&args);
        }
        // A Python client (or an unknown driver name) → the Python driver-client CLI.
        _ => {
            tracing::debug!(
                driver = first,
                "no native client; delegating to python driver-client CLI"
            );
            drop(session);
            delegate_to_python(&args);
        }
    }
}

/// Delegate to the per-crate Rust client CLI binary for a `rust:<crate>` client label — the
/// client-side mirror of the hub's per-crate host binary. `JMP_RUST_CLIENT_CLI` overrides the path;
/// else `<crate>-client` on `PATH`. The driver name + subcommand are forwarded and JUMPSTARTER_HOST
/// is inherited, so the spawned client connects + drives the typed client over native gRPC.
fn delegate_to_rust_client(label: &str, args: &[String]) -> ! {
    let bin = std::env::var("JMP_RUST_CLIENT_CLI").unwrap_or_else(|_| {
        let crate_name = label.strip_prefix("rust:").unwrap_or(label);
        format!("{crate_name}-client")
    });
    match Command::new(&bin).args(args).status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(e) => fail(&format!("cannot launch the rust client CLI ({bin}): {e}")),
    }
}

/// Delegate to the JVM client CLI — a `jumpstarter-jvm-client` start script running
/// `dev.jumpstarter.cli.JMain` (resolves the `jvm:<fqn>` client, runs its picocli command over native
/// gRPC). `JMP_JVM_CLIENT_CLI` overrides the launcher path; JUMPSTARTER_HOST is inherited.
fn delegate_to_jvm(args: &[String]) -> ! {
    let launcher = std::env::var("JMP_JVM_CLIENT_CLI")
        .unwrap_or_else(|_| "jumpstarter-jvm-client".to_string());
    match Command::new(&launcher).args(args).status() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(e) => fail(&format!(
            "cannot launch the jvm client CLI ({launcher}): {e}"
        )),
    }
}

/// The `(uuid, client-label)` of the top-level driver named `name`, from the report JSON.
fn client_for(report: &str, name: &str) -> Option<(String, String)> {
    let nodes: Vec<Json> = serde_json::from_str(report).ok()?;
    nodes.iter().find_map(|n| {
        let labels = n.get("labels")?;
        if labels.get("jumpstarter.dev/name").and_then(Json::as_str) == Some(name) {
            let uuid = n.get("uuid")?.as_str()?.to_string();
            let client = labels.get("jumpstarter.dev/client")?.as_str()?.to_string();
            Some((uuid, client))
        } else {
            None
        }
    })
}

/// Delegate the whole invocation to the Python driver-client CLI (`python -m jumpstarter_cli.j`).
fn delegate_to_python(args: &[String]) -> ! {
    let python = find_python();
    match Command::new(&python)
        .arg("-m")
        .arg("jumpstarter_cli.j")
        .args(args)
        .status()
    {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(e) => fail(&format!(
            "cannot launch the python driver-client CLI ({python}): {e}"
        )),
    }
}

fn fail(msg: &str) -> ! {
    eprintln!("Error: {msg}");
    std::process::exit(1);
}

/// Initialize tracing from `RUST_LOG` (default `info`), writing to stderr — mirrors the `jmp`
/// binary so the native `j` entrypoint honours the same logging configuration.
fn init_tracing() {
    use tracing_subscriber::EnvFilter;
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .try_init();
}

/// `JMP_DRIVER_HOST_PYTHON` wins, else the venv python sibling of this binary, else `python3`.
fn find_python() -> String {
    if let Ok(p) = std::env::var("JMP_DRIVER_HOST_PYTHON") {
        return p;
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["python3", "python"] {
                let candidate = dir.join(name);
                if candidate.is_file() {
                    return candidate.to_string_lossy().into_owned();
                }
            }
        }
    }
    "python3".to_string()
}
