//! Native `j` — the client-side driver CLI, the client-side mirror of the polyglot exporter hub.
//!
//! `j <driver> <cmd> <args>` drives a leased exporter's drivers. Native `j` connects via
//! `ClientSession`, reads the report, and **routes each driver to its client language** (from the
//! `jumpstarter.dev/client` label): a native Rust client runs **in-process** (no Python), while a
//! Python client class is delegated to the Python driver-client CLI (`python -m jumpstarter_cli.j`),
//! preserving its rich click CLI verbatim. A pure-native-client set needs no Python at all.

use std::process::Command;

use jumpstarter_core::ClientSession;
use serde_json::Value as Json;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    // Non-driver invocations (no args, a global flag, the `introspect` MCP side channel) go to the
    // Python driver-client CLI, which owns the top-level help + introspection.
    let first = args.first().map(String::as_str).unwrap_or("");
    if first.is_empty() || first.starts_with('-') || first == "introspect" {
        delegate_to_python(&args);
    }

    // A driver invocation: connect to route it to the owning client's language. Without a lease
    // (no JUMPSTARTER_HOST), let the Python CLI emit its "use inside a jmp shell" message.
    let host = match std::env::var("JUMPSTARTER_HOST") {
        Ok(h) => h,
        Err(_) => delegate_to_python(&args),
    };
    let session = match ClientSession::connect(host).await {
        Ok(s) => s,
        Err(e) => fail(&format!("connecting to exporter: {e}")),
    };
    let report = match session.get_report().await {
        Ok(r) => r,
        Err(e) => fail(&format!("fetching driver report: {e}")),
    };

    match client_for(&report, first) {
        // A native (Rust) client → drive it in-process via the native client registry, no Python.
        Some((uuid, label)) if label.starts_with("rust:") => {
            match jumpstarter_driver_example::run_client(&label, &args[1..], &session, &uuid).await {
                Some(code) => std::process::exit(code),
                None => fail(&format!("no native client is registered for `{label}`")),
            }
        }
        // A Python client (or an unknown driver name) → the Python driver-client CLI.
        _ => {
            drop(session);
            delegate_to_python(&args);
        }
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
