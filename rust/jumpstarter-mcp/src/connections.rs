//! Persistent connection manager for the MCP server (ports the Python `ConnectionManager`).
//!
//! A connection holds a leased exporter: an acquired lease + a served `JUMPSTARTER_HOST`
//! Unix socket, both on the Rust core (`jumpstarter_core::ControllerSession`). The `j`
//! subprocess tools (run + introspection) reach the exporter through that socket. The stored
//! `ControllerSession` and `LeaseTransport` are kept alive for the connection's lifetime so
//! the socket stays served; dropping the connection tears the listener down.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use jumpstarter_core::{ControllerSession, LeaseTransport};
use serde_json::{json, Value};
use tokio::sync::Mutex;

const ACQUISITION_TIMEOUT_SECS: u64 = 7200;

struct Connection {
    lease_name: String,
    exporter_name: String,
    socket_path: String,
    allow: Vec<String>,
    unsafe_drivers: bool,
    created_at: Instant,
    /// Release the lease on disconnect only when we created it (not when the caller
    /// connected to a pre-existing lease by id).
    should_release: bool,
    session: ControllerSession,
    /// Kept alive so the transport listener stays up; dropped on disconnect.
    _transport: Arc<LeaseTransport>,
}

/// The `j`-subprocess environment for one connection.
pub struct ConnEnv {
    pub lease_name: String,
    pub exporter_name: String,
    pub socket_path: String,
    pub allow: Vec<String>,
    pub unsafe_drivers: bool,
}

impl ConnEnv {
    /// The `JMP_DRIVERS_ALLOW` value ("UNSAFE", or the comma-joined allow-list).
    pub fn drivers_allow(&self) -> String {
        if self.unsafe_drivers {
            "UNSAFE".to_string()
        } else {
            self.allow.join(",")
        }
    }
}

#[derive(Clone, Default)]
pub struct ConnectionManager {
    conns: Arc<Mutex<HashMap<String, Connection>>>,
    counter: Arc<AtomicU64>,
}

impl ConnectionManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// Acquire/reuse a lease, serve its transport socket, and register the connection.
    /// Returns the connection summary JSON.
    #[allow(clippy::too_many_arguments)]
    pub async fn connect(
        &self,
        session: ControllerSession,
        allow: Vec<String>,
        unsafe_drivers: bool,
        lease_id: Option<String>,
        selector: Option<String>,
        exporter_name: Option<String>,
        duration_secs: u64,
    ) -> Result<Value, String> {
        let should_release = lease_id.is_none();
        tracing::info!(
            lease_id = ?lease_id,
            selector = ?selector,
            exporter = ?exporter_name,
            duration_secs,
            "connect"
        );
        let acquire_started = Instant::now();
        let acquired = session
            .acquire_lease(selector.clone(), exporter_name.clone(), lease_id, duration_secs, ACQUISITION_TIMEOUT_SECS)
            .await
            .map_err(|e| e.to_string())?;
        tracing::debug!(
            lease = %acquired.name,
            exporter = %acquired.exporter,
            elapsed = ?acquire_started.elapsed(),
            "lease acquired; serving transport"
        );
        let transport = session.serve_lease(acquired.name.clone()).await.map_err(|e| e.to_string())?;
        let socket_path = transport.jumpstarter_host().await.map_err(|e| e.to_string())?;
        tracing::debug!(lease = %acquired.name, socket = %socket_path, "transport served");

        let id = format!("{:08x}", self.counter.fetch_add(1, Ordering::Relaxed) + 1);
        tracing::info!(
            connection_id = %id,
            lease = %acquired.name,
            exporter = %acquired.exporter,
            socket = %socket_path,
            "connected"
        );
        let info = json!({
            "connection_id": id,
            "lease_name": acquired.name,
            "exporter_name": acquired.exporter,
            "socket_path": socket_path,
            "note": "Use jmp_explore / jmp_drivers to discover commands, then jmp_run to execute.",
        });
        let conn = Connection {
            lease_name: acquired.name,
            exporter_name: acquired.exporter,
            socket_path,
            allow,
            unsafe_drivers,
            created_at: Instant::now(),
            should_release,
            session,
            _transport: transport,
        };
        self.conns.lock().await.insert(id, conn);
        Ok(info)
    }

    /// Tear down a connection: release the lease (if we created it) and drop the transport.
    pub async fn disconnect(&self, id: &str) -> Result<Value, String> {
        let conn = self
            .conns
            .lock()
            .await
            .remove(id)
            .ok_or_else(|| format!("No connection with id {id}"))?;
        tracing::info!(connection_id = %id, lease = %conn.lease_name, "disconnect");
        if conn.should_release {
            if let Err(e) = conn.session.release_lease(conn.lease_name.clone()).await {
                tracing::warn!(connection_id = %id, lease = %conn.lease_name, error = %e, "lease release failed on disconnect");
            }
        }
        Ok(json!({"connection_id": id, "status": "disconnected"}))
    }

    /// A structured list of active connections.
    pub async fn list(&self) -> Value {
        let conns = self.conns.lock().await;
        let arr: Vec<Value> = conns
            .iter()
            .map(|(id, c)| {
                json!({
                    "connection_id": id,
                    "lease_name": c.lease_name,
                    "exporter_name": c.exporter_name,
                    "socket_path": c.socket_path,
                    "uptime_seconds": c.created_at.elapsed().as_secs(),
                })
            })
            .collect();
        Value::Array(arr)
    }

    /// The subprocess environment for a connection (errors if the id is unknown).
    pub async fn env(&self, id: &str) -> Result<ConnEnv, String> {
        let conns = self.conns.lock().await;
        let c = conns.get(id).ok_or_else(|| format!("No connection with id {id}"))?;
        Ok(ConnEnv {
            lease_name: c.lease_name.clone(),
            exporter_name: c.exporter_name.clone(),
            socket_path: c.socket_path.clone(),
            allow: c.allow.clone(),
            unsafe_drivers: c.unsafe_drivers,
        })
    }
}
