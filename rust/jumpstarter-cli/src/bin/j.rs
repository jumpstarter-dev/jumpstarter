//! Native `j` — the client-side driver CLI entrypoint.
//!
//! `j <driver> <cmd> <args>` drives a leased exporter's drivers. This is the client-side mirror
//! of the polyglot exporter hub: the goal is for `j` to route each driver to its *client*
//! language (Python client classes via `jumpstarter.client_host`, native Rust clients in-process),
//! so a pure-native-client set needs no Python.
//!
//! This first increment is a thin native launcher: it finds the venv's python (the sibling of the
//! `j` binary, as a wheel installs `venv/bin/j` next to `venv/bin/python`) and delegates the whole
//! invocation to the Python driver-client CLI, preserving its rich per-driver click CLIs verbatim.
//! A later increment connects natively, reads the report, and routes per-driver by client language.

use std::process::Command;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let python = find_python();

    // Inherit stdio so an interactive `j shell`/driver CLI works; propagate the exit code.
    match Command::new(&python)
        .arg("-m")
        .arg("jumpstarter_cli.j")
        .args(&args)
        .status()
    {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("Error: cannot launch the python driver-client CLI ({python}): {e}");
            std::process::exit(1);
        }
    }
}

/// The python interpreter to run the driver-client CLI with: `JMP_DRIVER_HOST_PYTHON` wins, else
/// the venv python sibling of this binary (`venv/bin/j` → `venv/bin/python3`), else `python3`.
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
