//! Programmatic controller/lease facade — the minimal surface the language bindings need to
//! drive a lease lifecycle without the `jmp` CLI: connect to the controller, acquire/release
//! a lease, and serve a local transport socket the language's client connects to
//! (`JUMPSTARTER_HOST`). It wraps the same `jumpstarter-lease` primitives the Rust `jmp
//! shell` uses — [`ControllerClient`], [`lease::acquire`], [`transport::serve_default`] — so
//! `jumpstarter-testing` / MCP get identical behavior to the CLI without Python gRPC.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use jumpstarter_lease::lease::{self, AcquiredLease, CreateLeaseParams, LeaseProvider, LeaseTiming};
use jumpstarter_lease::transport::{self, TransportHost};
use jumpstarter_lease::ControllerClient;
use jumpstarter_config::{ClientConfig, ObjectMeta, TlsConfig};
use jumpstarter_protocol::client_v1;
use tokio::sync::Mutex;

use crate::error::ControllerError;

/// A connected controller session bound to one client identity. Cheap to hold; each lease
/// op is an independent RPC.
pub struct ControllerSession {
    inner: ControllerClient,
    tls: TlsConfig,
    client_name: String,
}

impl ControllerSession {
    /// Connect + authenticate to the controller using explicit connection fields (so the
    /// language can pass an in-memory or freshly-token-refreshed config, not just a path).
    pub async fn connect(
        endpoint: String,
        token: Option<String>,
        ca: String,
        tls_insecure: bool,
        namespace: String,
        name: String,
    ) -> Result<Self, ControllerError> {
        let mut metadata = ObjectMeta::new(name.clone());
        metadata.namespace = Some(namespace);
        let mut config = ClientConfig::new(metadata);
        config.endpoint = Some(endpoint);
        config.token = token.filter(|t| !t.is_empty());
        config.tls = TlsConfig { ca, insecure: tls_insecure };
        let inner = ControllerClient::connect(&config).await?;
        Ok(Self { inner, tls: config.tls, client_name: name })
    }

    /// Acquire a lease (the full FSM: create-or-reuse, poll until Ready). Returns the lease
    /// name + the assigned exporter name.
    pub async fn acquire_lease(
        &self,
        selector: Option<String>,
        exporter_name: Option<String>,
        existing_name: Option<String>,
        duration_secs: u64,
        acquisition_timeout_secs: u64,
    ) -> Result<AcquiredLease, ControllerError> {
        let params = CreateLeaseParams {
            selector,
            exporter_name,
            duration: Duration::from_secs(duration_secs),
            begin_time: None,
            lease_id: None,
            tags: BTreeMap::new(),
        };
        let timing = LeaseTiming {
            poll_interval: Duration::from_secs(5),
            acquisition_timeout: Duration::from_secs(acquisition_timeout_secs),
        };
        let started = Instant::now();
        let acquired = lease::acquire(
            &self.inner,
            params,
            existing_name,
            Some(self.client_name.as_str()),
            timing,
        )
        .await?;
        tracing::info!(
            lease = %acquired.name,
            client = %self.client_name,
            exporter = %acquired.exporter,
            elapsed = ?started.elapsed(),
            "lease acquired"
        );
        Ok(acquired)
    }

    /// Release (delete) a lease by name.
    pub async fn release_lease(&self, name: String) -> Result<(), ControllerError> {
        self.inner.delete_lease(&name).await?;
        Ok(())
    }

    /// Start a local transport listener bridged to the leased exporter. The returned handle
    /// exposes the `JUMPSTARTER_HOST` socket path the language's client connects to; dropping
    /// or closing it tears the listener down and removes the socket.
    pub async fn serve_lease(&self, name: String) -> Result<Arc<LeaseTransport>, ControllerError> {
        let host = transport::serve_default(self.inner.clone(), name, self.tls.clone()).await?;
        Ok(Arc::new(LeaseTransport { inner: Mutex::new(Some(host)) }))
    }

    /// List exporters (all pages) as a JSON array — the language binding `json.loads` it.
    /// `filter` is the full label-selector string. Each entry: `{name, labels, online,
    /// status}` (status = the enum name, or `null` when unspecified).
    pub async fn list_exporters_json(&self, filter: Option<String>) -> Result<String, ControllerError> {
        let exporters = self.inner.list_exporters(filter.as_deref()).await?;
        let arr: Vec<serde_json::Value> = exporters.iter().map(exporter_to_json).collect();
        Ok(serde_json::Value::Array(arr).to_string())
    }

    /// List leases (all pages) as a JSON array. Each entry: `{name, client, exporter,
    /// selector, exporter_name, tags, conditions:[{type,status}], begin_time_epoch,
    /// end_time_epoch, duration_seconds}` (timestamps are Unix epoch seconds; the binding
    /// formats them). `only_active` excludes expired leases.
    pub async fn list_leases_json(
        &self,
        filter: Option<String>,
        only_active: bool,
        tag_filter: Option<String>,
    ) -> Result<String, ControllerError> {
        let leases = self
            .inner
            .list_leases(filter.as_deref(), only_active, tag_filter.as_deref())
            .await?;
        let arr: Vec<serde_json::Value> = leases.iter().map(lease_to_json).collect();
        Ok(serde_json::Value::Array(arr).to_string())
    }

    /// Create a lease (does not wait for it to become Ready — that's `acquire_lease`).
    /// Returns the bare created lease name.
    pub async fn create_lease(
        &self,
        duration_secs: u64,
        selector: Option<String>,
        exporter_name: Option<String>,
        tags: BTreeMap<String, String>,
    ) -> Result<String, ControllerError> {
        let params = CreateLeaseParams {
            selector,
            exporter_name,
            duration: Duration::from_secs(duration_secs),
            begin_time: None,
            lease_id: None,
            tags,
        };
        let lease = self.inner.create_lease_raw(&params).await?;
        Ok(leaf(&lease.name))
    }
}

/// The bare resource name (last path segment) of a `namespaces/x/<kind>/<name>` identifier.
fn leaf(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).to_string()
}

fn exporter_status_name(value: i32) -> &'static str {
    match value {
        1 => "OFFLINE",
        2 => "AVAILABLE",
        3 => "BEFORE_LEASE_HOOK",
        4 => "LEASE_READY",
        5 => "AFTER_LEASE_HOOK",
        6 => "BEFORE_LEASE_HOOK_FAILED",
        7 => "AFTER_LEASE_HOOK_FAILED",
        _ => "UNSPECIFIED",
    }
}

fn ts_epoch(ts: &prost_types::Timestamp) -> f64 {
    ts.seconds as f64 + ts.nanos as f64 / 1_000_000_000.0
}

fn dur_secs(d: &prost_types::Duration) -> f64 {
    d.seconds as f64 + d.nanos as f64 / 1_000_000_000.0
}

#[allow(deprecated)] // `online` is deprecated in favor of `status` but still surfaced (matches the CLI).
fn exporter_to_json(e: &client_v1::Exporter) -> serde_json::Value {
    let labels: serde_json::Map<String, serde_json::Value> = e
        .labels
        .iter()
        .map(|(k, v)| (k.clone(), serde_json::Value::String(v.clone())))
        .collect();
    serde_json::json!({
        "name": leaf(&e.name),
        "labels": labels,
        "online": e.online,
        "status": (e.status != 0).then(|| exporter_status_name(e.status)),
    })
}

fn lease_to_json(l: &client_v1::Lease) -> serde_json::Value {
    let conditions: Vec<serde_json::Value> = l
        .conditions
        .iter()
        .map(|c| serde_json::json!({ "type": c.r#type, "status": c.status }))
        .collect();
    let tags: serde_json::Map<String, serde_json::Value> = l
        .tags
        .iter()
        .map(|(k, v)| (k.clone(), serde_json::Value::String(v.clone())))
        .collect();
    serde_json::json!({
        "name": leaf(&l.name),
        "client": l.client.as_deref().filter(|s| !s.is_empty()).map(leaf),
        "exporter": l.exporter.as_deref().filter(|s| !s.is_empty()).map(leaf),
        "selector": l.selector,
        "exporter_name": l.exporter_name.as_deref().filter(|s| !s.is_empty()),
        "tags": tags,
        "conditions": conditions,
        "begin_time_epoch": l.effective_begin_time.as_ref().map(ts_epoch),
        "end_time_epoch": l.effective_end_time.as_ref().map(ts_epoch),
        "duration_seconds": l.duration.as_ref().map(dur_secs),
    })
}

/// A live transport listener for one lease. Holds the `jumpstarter-lease` `TransportHost`
/// (whose `Drop` aborts the listener + removes the socket); [`close`](Self::close) tears it
/// down deterministically when the language's `serve_unix` context manager exits.
pub struct LeaseTransport {
    inner: Mutex<Option<TransportHost>>,
}

impl LeaseTransport {
    /// The bare Unix socket path to export as `JUMPSTARTER_HOST`.
    pub async fn jumpstarter_host(&self) -> Result<String, ControllerError> {
        self.inner
            .lock()
            .await
            .as_ref()
            .map(TransportHost::jumpstarter_host)
            .ok_or_else(|| ControllerError::Other("transport already closed".into()))
    }

    /// Stop the listener and remove the socket (idempotent).
    pub async fn close(&self) {
        self.inner.lock().await.take();
    }
}
