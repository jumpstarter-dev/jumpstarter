//! The local-UDS transport host — the `JUMPSTARTER_HOST` server
//! (`client/lease.py:315-388` `handle_async` + `serve_unix_async`).
//!
//! Serves a temporary Unix socket; every accepted connection is `Dial`ed and
//! bridged to the exporter through the router (see [`crate::router`]). Setting
//! `JUMPSTARTER_HOST` to [`TransportHost::jumpstarter_host`] lets an unmodified
//! Python `j` / driver client run through this socket unchanged.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use jumpstarter_config::TlsConfig;
use tokio::net::{UnixListener, UnixStream};
use tracing::debug;

use crate::dial::DEFAULT_DIAL_TIMEOUT;
use crate::error::ClientError;
use crate::router;
use crate::service::ControllerClient;

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// A running transport host. Dropping it stops accepting and removes the socket.
pub struct TransportHost {
    dir: PathBuf,
    socket: PathBuf,
    task: tokio::task::JoinHandle<()>,
}

impl TransportHost {
    /// The path to export as `JUMPSTARTER_HOST` (a bare Unix socket path; the
    /// Python client prepends `unix://`, `client/client.py:78-82`).
    pub fn jumpstarter_host(&self) -> String {
        self.socket.to_string_lossy().into_owned()
    }
}

impl Drop for TransportHost {
    fn drop(&mut self) {
        self.task.abort();
        let _ = std::fs::remove_file(&self.socket);
        let _ = std::fs::remove_dir(&self.dir);
    }
}

fn temp_socket() -> Result<(PathBuf, PathBuf), ClientError> {
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    // Keep the path short: Unix socket paths are capped (~104 bytes on macOS).
    let dir = std::env::temp_dir().join(format!("jmp-{}-{nanos:x}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir)
        .map_err(|e| ClientError::Config(format!("cannot create socket dir: {e}")))?;
    let socket = dir.join("sock");
    Ok((dir, socket))
}

/// Acquire-free transport: serve a UDS that tunnels each connection to the
/// exporter behind `lease_name`. The caller must already hold the lease.
pub async fn serve(
    client: ControllerClient,
    lease_name: String,
    tls: TlsConfig,
    dial_timeout: Duration,
) -> Result<TransportHost, ClientError> {
    let (dir, socket) = temp_socket()?;
    let listener = UnixListener::bind(&socket)
        .map_err(|e| ClientError::Config(format!("cannot bind {}: {e}", socket.display())))?;

    let ctx = Arc::new(Bridge {
        client,
        lease_name,
        tls,
        dial_timeout,
    });

    let task = tokio::spawn(async move {
        let mut conn_id: u64 = 0;
        loop {
            match listener.accept().await {
                Ok((stream, _)) => {
                    conn_id = conn_id.wrapping_add(1);
                    let conn = conn_id;
                    let ctx = ctx.clone();
                    debug!(lease_name = %ctx.lease_name, conn, "transport connection accepted");
                    tokio::spawn(async move {
                        if let Err(e) = ctx.handle(conn, stream).await {
                            tracing::warn!(lease_name = %ctx.lease_name, conn, error = %e, "router bridge failed");
                        }
                    });
                }
                Err(e) => {
                    tracing::warn!("accept on transport socket failed: {e}");
                    break;
                }
            }
        }
    });

    Ok(TransportHost { dir, socket, task })
}

/// Convenience wrapper using the default 30 s dial timeout.
pub async fn serve_default(
    client: ControllerClient,
    lease_name: String,
    tls: TlsConfig,
) -> Result<TransportHost, ClientError> {
    serve(client, lease_name, tls, DEFAULT_DIAL_TIMEOUT).await
}

struct Bridge {
    client: ControllerClient,
    lease_name: String,
    tls: TlsConfig,
    dial_timeout: Duration,
}

impl Bridge {
    async fn handle(&self, conn: u64, stream: UnixStream) -> Result<(), ClientError> {
        debug!(
            lease_name = %self.lease_name,
            conn,
            dial_timeout = ?self.dial_timeout,
            "dialing exporter for transport connection"
        );
        let dial = self
            .client
            .dial(&self.lease_name, self.dial_timeout)
            .await?;
        debug!(
            lease_name = %self.lease_name,
            conn,
            endpoint = %dial.router_endpoint,
            "dial succeeded; opening router bridge"
        );
        let result =
            router::bridge(stream, &dial.router_endpoint, &dial.router_token, &self.tls).await;
        debug!(lease_name = %self.lease_name, conn, ok = result.is_ok(), "router bridge closed");
        result
    }
}
