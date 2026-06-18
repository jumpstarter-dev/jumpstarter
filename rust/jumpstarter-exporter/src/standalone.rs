//! Standalone exporter serving (`jmp run --tls-grpc-listener`): serve the driver
//! tree on a TCP port with no controller and no lease lifecycle, running the
//! before/afterLease hooks once (`exporter/exporter.py:serve_standalone_tcp`).
//!
//! beforeLease runs at startup → `LEASE_READY`; the server then runs until a
//! termination signal (SIGTERM/SIGINT) or a client `EndSession`, at which point the
//! afterLease hook runs and the process shuts down.

use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;

use jumpstarter_config::{ExporterConfig, YamlConfig};
use tokio::sync::{watch, Notify};
use tokio::task::JoinHandle;

use crate::control::{uds_channel, StatusReporter, StatusSnapshot};
use crate::driver_host::SlimHost;
use crate::hooks::{self, BeforeOutcome, HookContext};
use crate::session::{self, RoutingTable, SharedSession};
use crate::Error;

/// Serve the exporter at `config_path` on `bind` (plaintext h2c) until a termination
/// signal or `EndSession`, requiring `passphrase` from clients when set.
pub async fn serve_standalone_tcp(
    config_path: &Path,
    bind: SocketAddr,
    passphrase: Option<String>,
) -> Result<(), Error> {
    let config = ExporterConfig::load(config_path)
        .map_err(|e| Error::Config(format!("loading exporter config: {e}")))?;

    // The driver tree (Python slim host) + its routing table.
    let host = SlimHost::spawn(config_path).await?;
    let channel = uds_channel(host.socket()).await?;
    let routing = RoutingTable::build(channel).await?;

    // Pin the session watch channels — there is no controller/lease loop. The senders
    // are held for the serving lifetime so receivers never observe `Closed`.
    let (_routing_tx, routing_rx) = watch::channel(Some(Arc::new(routing)));
    let (status_tx, status_rx) = watch::channel(StatusSnapshot::default());
    let status_tx = Arc::new(status_tx);
    let end_session = Arc::new(Notify::new());
    let (_es_tx, es_rx) = watch::channel(Some(end_session.clone()));
    let hook_log = crate::logbuf::HookLog::new();
    let shared = SharedSession::new(routing_rx, status_rx, es_rx, hook_log.clone());

    // A short hook socket for the hook `j` commands.
    let hook_dir = std::env::temp_dir().join(format!("jmp-standalone-{}", std::process::id()));
    std::fs::create_dir_all(&hook_dir)
        .map_err(|e| Error::Config(format!("creating hook socket dir: {e}")))?;
    let hook_socket = hook_dir.join("hook");
    let hook_socket_str = hook_socket.to_string_lossy().into_owned();

    let (tcp_task, hook_task) = session::serve_standalone(shared, bind, &hook_socket, passphrase)?;
    tracing::info!(%bind, "standalone exporter listening");

    let mut reporter = StatusReporter::standalone(status_tx);
    let ctx = HookContext {
        hook_socket: &hook_socket_str,
        lease_name: "standalone",
        client_name: "",
        hook_log,
    };

    // beforeLease → LEASE_READY (or shut down on on_failure=exit).
    if hooks::run_before_lease(&mut reporter, config.hooks.before_lease.as_ref(), &ctx).await
        == BeforeOutcome::Exit
    {
        cleanup(tcp_task, hook_task, host, &hook_dir);
        return Err(Error::Config(
            "beforeLease hook failed (on_failure=exit)".to_string(),
        ));
    }

    wait_for_shutdown(&end_session).await;

    // afterLease on shutdown (the e2e checks the exporter's stderr for its output).
    hooks::run_after_lease(&mut reporter, config.hooks.after_lease.as_ref(), &ctx).await;

    cleanup(tcp_task, hook_task, host, &hook_dir);
    Ok(())
}

async fn wait_for_shutdown(end_session: &Notify) {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).expect("install SIGTERM handler");
        let mut interrupt = signal(SignalKind::interrupt()).expect("install SIGINT handler");
        tokio::select! {
            _ = term.recv() => tracing::info!("SIGTERM received, shutting down"),
            _ = interrupt.recv() => tracing::info!("SIGINT received, shutting down"),
            _ = end_session.notified() => tracing::info!("EndSession received, shutting down"),
        }
    }
    #[cfg(not(unix))]
    {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {},
            _ = end_session.notified() => {},
        }
    }
}

fn cleanup(tcp: JoinHandle<()>, hook: JoinHandle<()>, host: SlimHost, hook_dir: &Path) {
    tcp.abort();
    hook.abort();
    drop(host);
    let _ = std::fs::remove_dir_all(hook_dir);
}
