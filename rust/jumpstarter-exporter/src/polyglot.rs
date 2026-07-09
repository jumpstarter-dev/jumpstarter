//! The polyglot hub host factory: one driver-host subprocess per top-level `export:` entry.
//!
//! [`PolyglotHostFactory`] is the exporter's [`HostFactory`] for the per-driver model. For
//! each top-level entry it writes a single-entry config, spawns a driver host in the entry's
//! `runtime` (a Python `jumpstarter_exporter_host` subprocess today; native Rust in #62),
//! dials it over a private UDS, and federates them through a [`RoutingBackend`]. Each entry's
//! subtree stays cohesive in one host; distinct entries run in parallel, in any language.
//!
//! The hub is language-neutral: it never embeds a language runtime, it only spawns hosts and
//! speaks the driver-host gRPC seam. A pure-native driver set spawns no Python at all.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};

use jumpstarter_config::{ExporterConfig, HookConfig, YamlConfig};
use tokio::process::{Child, Command};

use crate::backend::{DriverBackend, HostFactory, HostGuard};
use crate::driver_host::python_interpreter;
use crate::routing::{HostedEntry, RoutingBackend};
use crate::Error;

/// How long to wait for a freshly-spawned driver host to start serving its UDS.
const HOST_READY_TIMEOUT: Duration = Duration::from_secs(30);

/// One spawned driver-host subprocess. Dropping it SIGKILLs the child (`kill_on_drop`).
struct EntryHost {
    _child: Child,
}

impl Drop for EntryHost {
    fn drop(&mut self) {
        let _ = self._child.start_kill();
    }
}

/// Held for the lease lifetime: the spawned per-entry hosts + the temp dir of their configs
/// and sockets. Dropped at lease end — each host is SIGKILLed and the temp dir removed.
struct PolyglotGuard {
    _hosts: Vec<EntryHost>,
    dir: PathBuf,
}

impl Drop for PolyglotGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

/// Provisions a per-lease driver tree as one host per top-level `export:` entry.
pub struct PolyglotHostFactory {
    config_path: PathBuf,
}

impl PolyglotHostFactory {
    pub fn new(config_path: PathBuf) -> Self {
        Self { config_path }
    }
}

/// The effective runtime for a top-level entry: a `Base`'s explicit/inferred `runtime`,
/// else `python` (composites/proxies are Python driver classes).
fn entry_runtime(instance: &jumpstarter_config::DriverInstance) -> String {
    match instance {
        jumpstarter_config::DriverInstance::Base(b) => b.effective_runtime().to_string(),
        _ => "python".to_string(),
    }
}

/// A `Base` entry's `type:` (the native-host resolver needs the `rust:<crate>` suffix to pick a
/// per-crate host binary); `None` for composites/proxies (always Python-hosted).
fn base_type(instance: &jumpstarter_config::DriverInstance) -> Option<&str> {
    match instance {
        jumpstarter_config::DriverInstance::Base(b) => Some(b.r#type.as_str()),
        _ => None,
    }
}

/// A `Base` entry's explicit `host:` launcher (bin + extra args), if any — lets one exporter run
/// several *different* hosts of a runtime (e.g. two JVM modules' start scripts) or mix runtimes.
/// `None` for composites/proxies.
fn base_host(
    instance: &jumpstarter_config::DriverInstance,
) -> Option<&jumpstarter_config::HostSpec> {
    match instance {
        jumpstarter_config::DriverInstance::Base(b) => b.host.as_ref(),
        _ => None,
    }
}

/// A short unique temp dir under the system temp (Unix socket paths are length-capped, so we
/// keep the per-host `<i>.sock` / `<i>.yaml` names tiny).
fn host_dir() -> Result<PathBuf, Error> {
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let dir = std::env::temp_dir().join(format!("jmp-hub-{}-{nanos:x}", std::process::id()));
    std::fs::create_dir_all(&dir)
        .map_err(|e| Error::Config(format!("creating hub temp dir: {e}")))?;
    Ok(dir)
}

#[tonic::async_trait]
impl HostFactory for PolyglotHostFactory {
    async fn provision(&self) -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), Error> {
        let config = ExporterConfig::load(&self.config_path)
            .map_err(|e| Error::Config(format!("loading exporter config: {e}")))?;
        let dir = host_dir()?;

        // Build a provisioning future per entry, then run them all **concurrently**. Each entry's
        // (spawn Python host + dial its UDS until it serves) is independent and I/O-bound, so the
        // wall-clock collapses from the sum of all hosts to the slowest single host — the dominant
        // cost of exporter startup (registration spawns the whole tree to assemble `GetReport`).
        let provisions = config
            .export
            .iter()
            .enumerate()
            .map(|(index, (name, instance))| {
                let runtime = entry_runtime(instance);
                let driver_type = base_type(instance).map(str::to_string);
                let host_spec = base_host(instance).cloned();
                let uds = dir.join(format!("{index}.sock"));
                // A single-entry config (drop hooks — the hub runs lease hooks, not the host),
                // streamed to the host on stdin (no temp file on disk).
                let mut entry_config = config.clone();
                entry_config.export = BTreeMap::from([(name.clone(), instance.clone())]);
                entry_config.hooks = HookConfig::default();
                let name = name.clone();
                async move {
                    let yaml = entry_config
                        .to_yaml()
                        .map_err(|e| Error::Config(format!("serializing entry config: {e}")))?;
                    let host = spawn_entry_host(
                        &runtime,
                        driver_type.as_deref(),
                        host_spec.as_ref(),
                        &yaml,
                        &uds,
                    )
                    .await?;
                    let backend = dial_with_retry(&uds, HOST_READY_TIMEOUT).await?;
                    Ok::<(EntryHost, HostedEntry), Error>((host, HostedEntry { name, backend }))
                }
            });
        // try_join_all preserves input order and short-circuits on the first error.
        let provisioned = futures::future::try_join_all(provisions).await?;
        let mut hosts: Vec<EntryHost> = Vec::with_capacity(provisioned.len());
        let mut entries: Vec<HostedEntry> = Vec::with_capacity(provisioned.len());
        for (host, entry) in provisioned {
            hosts.push(host);
            entries.push(entry);
        }

        let root_uuid = uuid::Uuid::new_v4().to_string();
        let backend = RoutingBackend::build(root_uuid, config.description.clone(), entries)
            .await
            .map_err(|s| Error::Config(format!("federating driver hosts: {s}")))?;

        let guard = PolyglotGuard { _hosts: hosts, dir };
        Ok((Arc::new(backend), Box::new(guard)))
    }
}

/// Spawn a driver host for one entry in the given runtime, serving the entry's subtree on
/// `uds`. Every host obeys the same language-neutral contract: it reads the single-entry config
/// on stdin and serves the driver-host seam on `--serve <uds>`. The hub never embeds a runtime —
/// a pure-native or pure-JVM driver set spawns no Python at all.
///
/// - **Python**: `python -m jumpstarter.exporter_host` (the embedded `jumpstarter_core` serves the
///   seam); `type:` is a dotted import path.
/// - **Rust**: a per-crate `jumpstarter-driver-<crate>-host` for `type: rust:<crate>` if one is on
///   `PATH`, else the in-tree `jmp-rust-host` registry binary (see [`rust_host_bin`]).
/// - **JVM**: a `jumpstarter-exporter-host` start script (over UniFFI `serve_driver_host`);
///   `type: jvm:<service-fqn>`.
async fn spawn_entry_host(
    runtime: &str,
    driver_type: Option<&str>,
    host_spec: Option<&jumpstarter_config::HostSpec>,
    yaml: &str,
    uds: &std::path::Path,
) -> Result<EntryHost, Error> {
    let uds_disp = uds.to_string_lossy();
    tracing::info!(runtime, entry = %uds_disp, uds = %uds_disp, "spawning driver host");
    // Build the host command. Every host reads its single-entry config on stdin and serves the
    // driver-host seam on `--serve <uds>`; the hub never embeds a runtime.
    let mut command = if let Some(spec) = host_spec {
        // An explicit per-entry launcher (`host: { bin, args }`): run `<bin> <args...> --serve
        // <uds>` for ANY runtime, so one exporter can federate several different/mixed hosts. A JVM
        // bin may be a not-yet-built Gradle `installDist` output — build it on demand.
        let bin = if runtime == "jvm" {
            resolve_jvm_host_exe(Some(spec.bin())).await
        } else {
            spec.bin().to_string()
        };
        let mut c = Command::new(bin);
        c.args(spec.args());
        c.arg("--serve").arg(uds);
        c
    } else {
        match runtime {
            // Python: `python -m jumpstarter.exporter_host` in the venv (JMP_DRIVER_HOST_PYTHON).
            "python" => {
                let mut c = Command::new(python_interpreter());
                c.arg("-m")
                    .arg("jumpstarter.exporter_host")
                    .arg("--serve")
                    .arg(uds);
                c
            }
            // Native Rust: a prebuilt per-crate host on PATH, else (local-dev Cargo workspace) build
            // on demand, else the `jmp-rust-host` registry (JMP_RUST_DRIVER_HOST overrides).
            "rust" => {
                let mut c = Command::new(rust_host_bin(driver_type).await);
                c.arg("--serve").arg(uds);
                c
            }
            // JVM (Java/Kotlin): `jumpstarter-exporter-host` (JMP_JVM_DRIVER_HOST overrides); it
            // calls the UniFFI `serve_driver_host` with a reflectively loaded `type: jvm:<fqn>`.
            "jvm" => {
                let mut c = Command::new(resolve_jvm_host_exe(None).await);
                c.arg("--serve").arg(uds);
                c
            }
            other => return Err(Error::Config(format!(
                "driver runtime `{other}` is not supported (expected `python`, `rust`, or `jvm`)"
            ))),
        }
    };
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        // The host watches *this hub's pid* (not its own parent) so it self-reaps if the hub dies
        // ungracefully — robust even when the host is reparented to init. See the host watchdogs:
        // `jumpstarter.exporter_host` (Python) and `jumpstarter_exporter::exit_when_orphaned`.
        .env("JMP_HUB_PID", std::process::id().to_string())
        .kill_on_drop(true);
    let mut child = command
        .spawn()
        .map_err(|e| Error::Config(format!("spawning {runtime} driver host: {e}")))?;
    tracing::debug!(runtime, uds = %uds_disp, pid = child.id(), "driver host process spawned");

    // Stream the single-entry config to the host on stdin, then close it (EOF).
    if let Some(mut stdin) = child.stdin.take() {
        use tokio::io::AsyncWriteExt as _;
        stdin
            .write_all(yaml.as_bytes())
            .await
            .map_err(|e| Error::Config(format!("writing config to driver host: {e}")))?;
        let _ = stdin.shutdown().await;
    }
    Ok(EntryHost { _child: child })
}

/// Resolve the default host binary for a `rust:` driver entry (when no per-entry `host:` is set).
/// Native hosts are **pre-compiled, standalone binaries** — the hub spawns them by name and never
/// links a driver registry into the `jmp` CLI itself:
///   1. `JMP_RUST_DRIVER_HOST` — a process-wide override path, else
///   2. the crate's own binary on `PATH` (the entry names the crate in full, `type: rust:<crate>`,
///      and the crate's `src/main.rs` is the host) — independent of `jmp`, else
///   3. the in-tree `jmp-rust-host` registry binary (also a separate process; links the bundled
///      example drivers) as the fallback.
async fn rust_host_bin(driver_type: Option<&str>) -> String {
    // 1. The process-wide env override (a path to a prebuilt host).
    if let Ok(p) = std::env::var("JMP_RUST_DRIVER_HOST") {
        return p;
    }
    // 2. Prefer a prebuilt per-crate `jumpstarter-driver-<crate>-host` on PATH (production /
    //    already-installed) — the resolution is the pure, unit-tested core below.
    let prebuilt = resolve_rust_host(driver_type, |bin| find_on_path(bin).is_some());
    if prebuilt != REGISTRY_HOST {
        return prebuilt;
    }
    // 3. Not prebuilt: in a local-dev Cargo workspace, build the per-crate host then exec the
    //    artifact directly (NOT `cargo run` — the built binary must be the hub's *direct* child so
    //    `kill_on_drop` / the `JMP_HUB_PID` watchdog reap it at lease end).
    if let Some(crate_name) = rust_crate_name(driver_type) {
        if let Some(ws) = cargo_workspace_root() {
            if let Some(exe) = dev_build_cargo_bin(&ws, crate_name).await {
                return exe.to_string_lossy().into_owned();
            }
        }
    }
    // 4. The in-tree registry binary (also a standalone process; links the bundled example drivers).
    REGISTRY_HOST.to_string()
}

/// The in-tree fallback native host (the `make_driver` registry mega-binary).
const REGISTRY_HOST: &str = "jmp-rust-host";

/// The fully-qualified Cargo crate name in a `rust:<crate>` type (e.g.
/// `rust:jumpstarter-driver-power-example`), validated (non-empty, no path separators — a `type`
/// names a crate, not a path); `None` for non-`rust:` types.
fn rust_crate_name(driver_type: Option<&str>) -> Option<&str> {
    driver_type
        .and_then(|t| t.strip_prefix("rust:"))
        .filter(|c| !c.is_empty() && !c.contains('/'))
}

/// The per-crate native host binary name: the crate's own **default binary** (named after the
/// package — its `src/main.rs` is the host via `jumpstarter_driver::driver_host!`). A driver
/// crate is named in full (`rust:<crate>`) and ships this binary, so the per-crate path links no
/// driver code into `jmp` (and matches `cargo install <crate>` → `<crate>` on `PATH`).
fn rust_host_bin_name(crate_name: &str) -> String {
    crate_name.to_string()
}

/// Pure resolution (no env/`PATH`/build access — `exists` is injected so it is unit-testable): the
/// crate's own binary when it exists on `PATH`, else the [`REGISTRY_HOST`] binary.
fn resolve_rust_host(driver_type: Option<&str>, exists: impl Fn(&str) -> bool) -> String {
    if let Some(crate_name) = rust_crate_name(driver_type) {
        let per_crate = rust_host_bin_name(crate_name);
        if exists(&per_crate) {
            tracing::debug!(crate_name, bin = %per_crate, "using per-crate native host binary");
            return per_crate;
        }
    }
    REGISTRY_HOST.to_string()
}

/// Locate the local-dev Cargo workspace to build a per-crate host in. This is **not** specific to
/// the Jumpstarter monorepo — it supports active development of any out-of-tree driver/client crate:
///   1. `JMP_RUST_DRIVER_WORKSPACE` — an explicit workspace root (for when the hub's cwd is not the
///      driver's workspace), else
///   2. the nearest ancestor of the current directory whose `Cargo.toml` declares a `[workspace]`.
///
/// A production/install layout has neither, so the dev build fallback never engages there and we
/// strictly prefer prebuilt artifacts.
fn cargo_workspace_root() -> Option<PathBuf> {
    if let Some(ws) = std::env::var_os("JMP_RUST_DRIVER_WORKSPACE") {
        let ws = PathBuf::from(ws);
        if ws.join("Cargo.toml").is_file() {
            return Some(ws);
        }
        tracing::warn!(workspace = %ws.display(), "JMP_RUST_DRIVER_WORKSPACE has no Cargo.toml; ignoring");
    }
    let mut dir = std::env::current_dir().ok()?;
    loop {
        let manifest = dir.join("Cargo.toml");
        if manifest.is_file()
            && std::fs::read_to_string(&manifest)
                .map(|t| t.contains("[workspace]"))
                .unwrap_or(false)
        {
            return Some(dir);
        }
        if !dir.pop() {
            return None;
        }
    }
}

/// Local-dev fallback: `cargo build -p <crate> --bin <crate>` in `workspace`, returning the built
/// executable path if it now exists. The fully-qualified crate name lets Cargo resolve the package
/// directly (`-p`) and its default bin (`--bin <crate>`), so this works for any in-workspace driver
/// crate. `None` when there is no such crate/bin target (or the build failed with no usable prior
/// artifact) — the caller then uses the registry fallback. Compiles on first use and is cached by
/// Cargo afterward; build output streams to the hub's stderr.
async fn dev_build_cargo_bin(workspace: &std::path::Path, crate_name: &str) -> Option<PathBuf> {
    let bin = rust_host_bin_name(crate_name);
    let target_dir = std::env::var_os("CARGO_TARGET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| workspace.join("target"));
    let exe = target_dir.join("debug").join(&bin);
    tracing::info!(
        crate_name, bin = %bin, workspace = %workspace.display(),
        "native host not prebuilt; building via `cargo build -p` (local dev)"
    );
    let status = Command::new("cargo")
        .current_dir(workspace)
        .arg("build")
        .arg("-p")
        .arg(crate_name)
        .arg("--bin")
        .arg(&bin)
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .await;
    match status {
        Ok(s) if s.success() && is_executable(&exe) => Some(exe),
        // A non-`rust:` bin or a build error: fall back to the registry, but reuse a usable prior
        // artifact if one is present (a transient error shouldn't drop a working host).
        Ok(s) => {
            tracing::warn!(
                bin, code = ?s.code(), exe = %exe.display(),
                "`cargo build` did not produce the host binary; falling back to the registry host"
            );
            is_executable(&exe).then_some(exe)
        }
        Err(e) => {
            tracing::warn!(bin, error = %e, "could not run `cargo` for the dev host build; falling back to the registry host");
            None
        }
    }
}

/// The JVM driver-host launcher name (an `application`-plugin start script).
const JVM_HOST: &str = "jumpstarter-exporter-host";

/// Resolve (building in local dev if needed) the JVM host launcher to spawn with `--serve <uds>`:
///   1. `JMP_JVM_DRIVER_HOST` — an explicit start-script path. If it exists, use it; if it does
///      **not** yet exist but its path is a Gradle `installDist` output, build it then exec it
///      (local dev — the JVM reflection model decouples the launcher from the driver `type:`, so the
///      module is recovered from the path, not derived from the type as for Rust).
///   2. `jumpstarter-exporter-host` on `PATH` (production / already-installed).
async fn resolve_jvm_host_exe(host_override: Option<&str>) -> String {
    // A per-entry `host:` is the most specific; else the process-wide env. Either may point at a
    // not-yet-built Gradle `installDist` output, which we build on demand.
    let configured = host_override
        .map(str::to_string)
        .or_else(|| std::env::var("JMP_JVM_DRIVER_HOST").ok());
    if let Some(p) = configured {
        let path = PathBuf::from(&p);
        if path.exists() {
            return p;
        }
        if let Some(built) = dev_build_gradle_host(&path).await {
            return built;
        }
        return p; // let the spawn surface a clear ENOENT if we could not build it
    }
    JVM_HOST.to_string()
}

/// Local-dev fallback for the JVM host: the configured start-script path doesn't exist yet, so if it
/// is a Gradle `installDist` output (`…/<module>/build/install/<app>/bin/<app>`), run
/// `./gradlew :<module>:installDist` in the enclosing Gradle workspace, then return the path if it
/// now exists. Build output streams to the hub's stderr; cached by Gradle afterward.
async fn dev_build_gradle_host(script: &std::path::Path) -> Option<String> {
    let s = script.to_string_lossy();
    let idx = s.find("/build/install/")?;
    let module_dir = PathBuf::from(&s[..idx]); // …/<module>
    let module = module_dir.file_name()?.to_string_lossy().into_owned();
    // The Gradle workspace is the nearest ancestor with a `gradlew` wrapper.
    let mut workspace = module_dir.clone();
    while !workspace.join("gradlew").is_file() {
        if !workspace.pop() {
            return None;
        }
    }
    tracing::info!(
        module, workspace = %workspace.display(),
        "JVM host not built; running `gradlew :<module>:installDist` (local dev)"
    );
    let status = Command::new(workspace.join("gradlew"))
        .current_dir(&workspace)
        .arg("-q")
        .arg(format!(":{module}:installDist"))
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .await
        .ok()?;
    (status.success() && is_executable(script)).then(|| script.to_string_lossy().into_owned())
}

/// A minimal `which`: the first executable named `bin` on `PATH`, if any.
fn find_on_path(bin: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(bin))
        .find(|candidate| is_executable(candidate))
}

/// Whether `p` is a regular file with any execute bit set.
fn is_executable(p: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// Dial a driver host's UDS, retrying until it answers `GetReport` (the host binds its socket
/// asynchronously after spawn), or `timeout` elapses.
async fn dial_with_retry(
    uds: &std::path::Path,
    timeout: Duration,
) -> Result<Arc<dyn DriverBackend>, Error> {
    let uds = uds.to_string_lossy().into_owned();
    let deadline = Instant::now() + timeout;
    // The last connect / get_report failure, surfaced in the timeout error (and per-retry
    // at debug). Previously both were swallowed (`if let Ok` / `.is_ok()`), so a 30s timeout
    // gave only a generic message with no clue why the host never answered. The deadline read
    // is only reachable after a failure has set this, so the initial value is a fallback only.
    #[allow(unused_assignments)]
    let mut last_error: Option<String> = None;
    let mut attempt: u64 = 0;
    loop {
        attempt += 1;
        match crate::control::uds_channel(&uds).await {
            Ok(channel) => {
                // The hub↔driver-host byte plane always rides shared memory (the only supported
                // transport): `ShmChannelBackend` routes bulk router-stream bytes through an SPSC ring,
                // eliminating the second gRPC hop the polyglot model would otherwise add. Control RPCs
                // (get_report/driver_call/…) still ride the inner gRPC channel over this same UDS.
                let backend: Arc<dyn DriverBackend> =
                    Arc::new(crate::shm_backend::ShmChannelBackend::new(channel));
                match backend.get_report().await {
                    Ok(_) => {
                        tracing::debug!(%uds, attempt, "driver host ready");
                        return Ok(backend);
                    }
                    Err(e) => {
                        let msg = e.to_string();
                        tracing::debug!(%uds, attempt, error = %msg, "driver host get_report not ready; retrying");
                        last_error = Some(msg);
                    }
                }
            }
            Err(e) => {
                let msg = e.to_string();
                tracing::debug!(%uds, attempt, error = %msg, "driver host dial failed; retrying");
                last_error = Some(msg);
            }
        }
        if Instant::now() >= deadline {
            return Err(Error::Config(format!(
                "driver host on {uds} did not start serving within {}s (last error: {})",
                timeout.as_secs(),
                last_error.as_deref().unwrap_or("none")
            )));
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::resolve_rust_host;

    #[test]
    fn resolves_per_crate_then_falls_back_to_registry() {
        // No `rust:` type (composite/proxy) → the registry binary, regardless of `exists`.
        assert_eq!(resolve_rust_host(None, |_| true), "jmp-rust-host");

        // A `rust:<fully-qualified-crate>` whose own binary is on PATH → that pre-compiled binary
        // (the crate's `src/main.rs` host; no driver code linked into the CLI).
        assert_eq!(
            resolve_rust_host(Some("rust:jumpstarter-driver-power-example"), |b| b
                == "jumpstarter-driver-power-example"),
            "jumpstarter-driver-power-example"
        );

        // A `rust:<crate>` with no per-crate host on PATH → the `jmp-rust-host` registry fallback.
        assert_eq!(
            resolve_rust_host(Some("rust:jumpstarter-driver-echo"), |_| false),
            "jmp-rust-host"
        );

        // A Python-style dotted type is never a native host, even if a same-named binary exists.
        assert_eq!(
            resolve_rust_host(Some("jumpstarter_driver_power.driver.MockPower"), |_| true),
            "jmp-rust-host"
        );

        // A path-traversal suffix is rejected (a `type` names a crate, not a path).
        assert_eq!(
            resolve_rust_host(Some("rust:../evil"), |_| true),
            "jmp-rust-host"
        );
    }
}
