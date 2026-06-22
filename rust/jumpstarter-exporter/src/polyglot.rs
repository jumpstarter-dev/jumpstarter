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

use crate::backend::{ChannelBackend, DriverBackend, HostFactory, HostGuard};
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
    async fn provision(
        &self,
    ) -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), Error> {
        let config = ExporterConfig::load(&self.config_path)
            .map_err(|e| Error::Config(format!("loading exporter config: {e}")))?;
        let dir = host_dir()?;

        let mut hosts: Vec<EntryHost> = Vec::new();
        let mut entries: Vec<HostedEntry> = Vec::new();

        for (index, (name, instance)) in config.export.iter().enumerate() {
            let runtime = entry_runtime(instance);
            let uds = dir.join(format!("{index}.sock"));

            // A single-entry config (drop hooks — the hub runs lease hooks, not the host),
            // streamed to the host on stdin (no temp file on disk).
            let mut entry_config = config.clone();
            entry_config.export = BTreeMap::from([(name.clone(), instance.clone())]);
            entry_config.hooks = HookConfig::default();
            let yaml = entry_config
                .to_yaml()
                .map_err(|e| Error::Config(format!("serializing entry config: {e}")))?;

            let host = spawn_entry_host(&runtime, &yaml, &uds).await?;
            hosts.push(host);

            let backend = dial_with_retry(&uds, HOST_READY_TIMEOUT).await?;
            entries.push(HostedEntry {
                name: name.clone(),
                backend,
            });
        }

        let root_uuid = uuid::Uuid::new_v4().to_string();
        let backend = RoutingBackend::build(root_uuid, config.description.clone(), entries)
            .await
            .map_err(|s| Error::Config(format!("federating driver hosts: {s}")))?;

        let guard = PolyglotGuard {
            _hosts: hosts,
            dir,
        };
        Ok((Arc::new(backend), Box::new(guard)))
    }
}

/// Spawn a driver host for one entry in the given runtime, serving the entry's subtree on
/// `uds`. Python entries run `python -m jumpstarter.exporter_host --serve <uds>` with the
/// single-entry config streamed on stdin (the embedded `jumpstarter_core` serves the seam);
/// native Rust hosts arrive in #62.
async fn spawn_entry_host(
    runtime: &str,
    yaml: &str,
    uds: &std::path::Path,
) -> Result<EntryHost, Error> {
    match runtime {
        "python" => {
            let python = python_interpreter();
            let mut child = Command::new(&python)
                .arg("-m")
                .arg("jumpstarter.exporter_host")
                .arg("--serve")
                .arg(uds)
                .stdin(Stdio::piped())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .kill_on_drop(true)
                .spawn()
                .map_err(|e| {
                    Error::Config(format!("spawning python driver host ({python}): {e}"))
                })?;
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
        other => Err(Error::Config(format!(
            "driver runtime `{other}` is not yet supported (native Rust hosts land in a later step)"
        ))),
    }
}

/// Dial a driver host's UDS, retrying until it answers `GetReport` (the host binds its socket
/// asynchronously after spawn), or `timeout` elapses.
async fn dial_with_retry(uds: &PathBuf, timeout: Duration) -> Result<Arc<dyn DriverBackend>, Error> {
    let uds = uds.to_string_lossy().into_owned();
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(channel) = crate::control::uds_channel(&uds).await {
            let backend = ChannelBackend::new(channel);
            if backend.get_report().await.is_ok() {
                tracing::debug!(%uds, "driver host ready");
                return Ok(Arc::new(backend));
            }
        }
        if Instant::now() >= deadline {
            return Err(Error::Config(format!(
                "driver host on {uds} did not start serving within {}s",
                timeout.as_secs()
            )));
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}
