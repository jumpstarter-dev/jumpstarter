//! Programmatic controller/lease facade — the minimal surface the language bindings need to
//! drive a lease lifecycle without the `jmp` CLI: connect to the controller, acquire/release
//! a lease, and serve a local transport socket the language's client connects to
//! (`JUMPSTARTER_HOST`). It wraps the same `jumpstarter-client` primitives the Rust `jmp
//! shell` uses — [`ControllerClient`], [`lease::acquire`], [`transport::serve_default`] — so
//! `jumpstarter-testing` / MCP get identical behavior to the CLI without Python gRPC.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_client::lease::{self, AcquiredLease, CreateLeaseParams, LeaseProvider, LeaseTiming};
use jumpstarter_client::transport::{self, TransportHost};
use jumpstarter_client::ControllerClient;
use jumpstarter_config::{ClientConfig, ObjectMeta, TlsConfig};
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
        let acquired = lease::acquire(
            &self.inner,
            params,
            existing_name,
            Some(self.client_name.as_str()),
            timing,
        )
        .await?;
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
}

/// A live transport listener for one lease. Holds the `jumpstarter-client` `TransportHost`
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
