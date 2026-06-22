//! Picking the Python interpreter for the per-driver Python host and `.py` hooks.
//!
//! The exporter hosts each driver in its own subprocess via [`crate::polyglot`]; this module
//! only resolves which `python` to spawn for Python drivers and `.py` lease hooks.

/// Env var selecting the Python interpreter for the driver host (and for `.py`
/// hooks). Must have the `jumpstarter` package importable; defaults to `python3`.
const PYTHON_ENV: &str = "JMP_DRIVER_HOST_PYTHON";

/// The Python interpreter to invoke for the per-driver Python host and `.py` hooks.
///
/// `JMP_DRIVER_HOST_PYTHON` wins (the Python `jmp run` sets it to `sys.executable`). Otherwise,
/// the native `jmp` binary is installed alongside its venv's python (`venv/bin/jmp` +
/// `venv/bin/python3`), so prefer that sibling — a wheel-installed native `jmp` then spawns
/// per-driver Python hosts in its own environment without the venv being activated. Falls back
/// to `python3` on `PATH`.
pub(crate) fn python_interpreter() -> String {
    if let Ok(p) = std::env::var(PYTHON_ENV) {
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
