//! UniFFI bindings for the Jumpstarter Rust core — the `jumpstarter_core` Python
//! extension (and, from the same definition, future Kotlin/Swift).
//!
//! Thin per-binding layer over [`jumpstarter_core`]: declares the foreign [`DriverHost`]
//! trait Python implements and the exported [`run_exporter`] entry Python awaits, then
//! adapts them to the binding-agnostic `jumpstarter_core::{ForeignHostApi, ...}` and the
//! exporter's `HostFactory`. No business logic here — codec/report/framing live in
//! `jumpstarter-core`.
//!
//! ## Design notes (UniFFI constraints, learned from the spike + build)
//! - Foreign-trait param is `method_name`, never `method` (a UniFFI Python codegen bug
//!   shadows a param named `method`).
//! - Async foreign methods MUST NOT return foreign objects (`Arc<dyn Trait>`): the lifted
//!   handle is a non-`Send` `*const c_void` inside the future. So streams are **handle-
//!   based**: `streaming_open`/`open_stream` return an opaque `u64`, and Rust drives
//!   `streaming_next`/`stream_read`/… by that handle. The Python host keeps a registry.

uniffi::setup_scaffolding!();

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use jumpstarter_core::{
    DriverCallError, ForeignByteChannel, ForeignDriverHost, ForeignHostApi, ForeignResultStream,
    ForeignStreamOpen,
};
use jumpstarter_exporter::backend::{DriverHostBackend, HostFactory, HostGuard};

// ---------------------------------------------------------------------------
// FFI types
// ---------------------------------------------------------------------------

/// Driver-call error a foreign host raises; maps 1:1 to the core taxonomy and on to the
/// `tonic::Status` clients observe. The foreign adapter MUST raise one of these for every
/// failure (UniFFI panics on an undeclared error type).
#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum DriverError {
    #[error("unimplemented: {0}")]
    Unimplemented(String),
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("deadline exceeded: {0}")]
    DeadlineExceeded(String),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("unknown: {0}")]
    Unknown(String),
}

/// Error from [`run_exporter`].
#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum ExporterError {
    #[error("config error: {0}")]
    Config(String),
    #[error("runtime error: {0}")]
    Runtime(String),
}

/// A flat driver-tree node (one per `@export` driver instance).
#[derive(uniffi::Record)]
pub struct DriverNode {
    pub uuid: String,
    pub parent_uuid: Option<String>,
    pub labels: HashMap<String, String>,
    pub description: Option<String>,
    pub methods_description: HashMap<String, String>,
}

/// One resource initial-metadata entry to relay.
#[derive(uniffi::Record)]
pub struct MetadataEntry {
    pub key: String,
    pub value: String,
}

/// Result of [`DriverHost::open_stream`]: an opaque byte-channel handle + resource initial
/// metadata. Plain data (no foreign object), so it is safe to return from an async method.
#[derive(uniffi::Record)]
pub struct OpenStream {
    pub handle: u64,
    pub initial_metadata: Vec<MetadataEntry>,
}

// ---------------------------------------------------------------------------
// Foreign trait (implemented in Python, called by Rust)
// ---------------------------------------------------------------------------

/// The driver-level surface a foreign host implements. Args/results are plain JSON
/// strings; Rust applies the proto-`Value` codec. Streams are handle-based (see module
/// docs): the host registers each stream/channel under a `u64` it returns.
#[uniffi::export(with_foreign)]
#[async_trait]
pub trait DriverHost: Send + Sync {
    async fn describe(&self) -> Result<Vec<DriverNode>, DriverError>;

    async fn driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<String, DriverError>;

    /// Start a streaming `@export` call; returns a handle to pull results from.
    async fn streaming_open(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<u64, DriverError>;
    /// Next JSON result for the stream, or `None` at end.
    async fn streaming_next(&self, handle: u64) -> Result<Option<String>, DriverError>;
    /// Release the streaming-call handle.
    async fn streaming_close(&self, handle: u64) -> Result<(), DriverError>;

    /// Open a byte channel to an `@exportstream`/resource handle; returns the handle +
    /// the resource initial metadata.
    async fn open_stream(&self, request_json: String) -> Result<OpenStream, DriverError>;
    /// Next inbound payload for the channel, or `None` at EOF.
    async fn stream_read(&self, handle: u64) -> Result<Option<Vec<u8>>, DriverError>;
    /// Write one payload toward the driver.
    async fn stream_write(&self, handle: u64, data: Vec<u8>) -> Result<(), DriverError>;
    /// Signal client→driver EOF.
    async fn stream_close_write(&self, handle: u64) -> Result<(), DriverError>;
    /// Tear down the channel.
    async fn stream_close(&self, handle: u64) -> Result<(), DriverError>;
}

/// Produces a fresh [`DriverHost`] per lease (fresh driver tree). Implemented in Python.
/// `new_host` is **sync**: driver-tree construction (`instantiate()`) is synchronous, and
/// an async foreign method can't return a foreign object (`Arc<dyn DriverHost>`).
#[uniffi::export(with_foreign)]
pub trait DriverHostFactory: Send + Sync {
    fn new_host(&self) -> Result<Arc<dyn DriverHost>, DriverError>;
}

// ---------------------------------------------------------------------------
// Exported entry point
// ---------------------------------------------------------------------------

/// Run the exporter in-process against a Python `DriverHostFactory`. Python awaits this on
/// its event loop for the process lifetime; the whole Rust runtime (controller gRPC,
/// session server, lease FSM, hooks) runs inside the call, calling back into the foreign
/// host for driver dispatch.
#[uniffi::export(async_runtime = "tokio")]
pub async fn run_exporter(
    config_path: String,
    factory: Arc<dyn DriverHostFactory>,
) -> Result<(), ExporterError> {
    use jumpstarter_config::{ExporterConfig, YamlConfig};

    let config = ExporterConfig::load(&config_path)
        .map_err(|e| ExporterError::Config(format!("loading exporter config: {e}")))?;
    let host_factory: Arc<dyn HostFactory> = Arc::new(UniffiHostFactory { inner: factory });
    jumpstarter_exporter::run_with_factory(config, host_factory)
        .await
        .map_err(|e| ExporterError::Runtime(e.to_string()))
}

/// Run the Rust `jmp` CLI command tree from a forwarded argv (`args[0]` is the program
/// name), returning the process exit code. The language entrypoint forwards the pure-Rust
/// commands here — `shell`/`create`/`delete`/`update`/`get`/`admin`/`auth`/`login`/`config`/
/// `version`/`completion` — and keeps `run` (driver host) and `j` (driver clients) native,
/// reaching the core through the foreign-trait seam. The CLI prints its own output and never
/// terminates the host process (errors and `--help`/`--version` map to an exit code here).
#[uniffi::export(async_runtime = "tokio")]
pub async fn run_cli(args: Vec<String>) -> u8 {
    jumpstarter_cli::dispatch(args).await
}

// ---------------------------------------------------------------------------
// Adapters: FFI trait -> jumpstarter-core / exporter seams
// ---------------------------------------------------------------------------

fn to_core_err(e: DriverError) -> DriverCallError {
    match e {
        DriverError::Unimplemented(m) => DriverCallError::Unimplemented(m),
        DriverError::InvalidArgument(m) => DriverCallError::InvalidArgument(m),
        DriverError::DeadlineExceeded(m) => DriverCallError::DeadlineExceeded(m),
        DriverError::NotFound(m) => DriverCallError::NotFound(m),
        DriverError::Unknown(m) => DriverCallError::Unknown(m),
    }
}

fn to_core_node(n: DriverNode) -> jumpstarter_core::DriverNode {
    jumpstarter_core::DriverNode {
        uuid: n.uuid,
        parent_uuid: n.parent_uuid,
        labels: n.labels,
        description: n.description,
        methods_description: n.methods_description,
    }
}

/// Wraps a foreign `DriverHostFactory` as the exporter's `HostFactory`.
struct UniffiHostFactory {
    inner: Arc<dyn DriverHostFactory>,
}

#[async_trait]
impl HostFactory for UniffiHostFactory {
    async fn provision(
        &self,
    ) -> Result<(Arc<dyn DriverHostBackend>, HostGuard), jumpstarter_exporter::Error> {
        let host = self
            .inner
            .new_host()
            .map_err(|e| jumpstarter_exporter::Error::Config(format!("new_host failed: {e}")))?;
        let api: Arc<dyn ForeignHostApi> = Arc::new(UniffiHostApi {
            inner: host.clone(),
        });
        let backend: Arc<dyn DriverHostBackend> = Arc::new(ForeignDriverHost::new(api));
        // Hold the DriverHost Arc as the lease guard so the foreign tree's lifetime is
        // explicit (dropped at lease end alongside the backend).
        Ok((backend, Box::new(host)))
    }
}

/// Wraps a foreign `DriverHost` as `jumpstarter_core::ForeignHostApi`.
struct UniffiHostApi {
    inner: Arc<dyn DriverHost>,
}

#[async_trait]
impl ForeignHostApi for UniffiHostApi {
    async fn describe(&self) -> Result<Vec<jumpstarter_core::DriverNode>, DriverCallError> {
        let nodes = self.inner.describe().await.map_err(to_core_err)?;
        Ok(nodes.into_iter().map(to_core_node).collect())
    }

    async fn driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<String, DriverCallError> {
        self.inner
            .driver_call(uuid, method_name, args_json)
            .await
            .map_err(to_core_err)
    }

    async fn streaming_driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<Arc<dyn ForeignResultStream>, DriverCallError> {
        let handle = self
            .inner
            .streaming_open(uuid, method_name, args_json)
            .await
            .map_err(to_core_err)?;
        Ok(Arc::new(HandleResultStream {
            host: self.inner.clone(),
            handle,
        }))
    }

    async fn open_stream(&self, request_json: String) -> Result<ForeignStreamOpen, DriverCallError> {
        let opened = self.inner.open_stream(request_json).await.map_err(to_core_err)?;
        let channel: Arc<dyn ForeignByteChannel> = Arc::new(HandleByteChannel {
            host: self.inner.clone(),
            handle: opened.handle,
        });
        let initial_metadata = opened
            .initial_metadata
            .into_iter()
            .map(|e| (e.key, e.value))
            .collect();
        Ok(ForeignStreamOpen {
            channel,
            initial_metadata,
        })
    }
}

/// A `ForeignResultStream` backed by a `(host, handle)` pair — drives `streaming_next`.
struct HandleResultStream {
    host: Arc<dyn DriverHost>,
    handle: u64,
}

#[async_trait]
impl ForeignResultStream for HandleResultStream {
    async fn next(&self) -> Result<Option<String>, DriverCallError> {
        let item = self.host.streaming_next(self.handle).await.map_err(to_core_err)?;
        if item.is_none() {
            // End of stream — release the handle (best effort).
            let _ = self.host.streaming_close(self.handle).await;
        }
        Ok(item)
    }
}

// ---------------------------------------------------------------------------
// Client side: ClientSession (Rust object Python calls into) — the consumer mirror
// ---------------------------------------------------------------------------

fn from_core_err(e: DriverCallError) -> DriverError {
    match e {
        DriverCallError::Unimplemented(m) => DriverError::Unimplemented(m),
        DriverCallError::InvalidArgument(m) => DriverError::InvalidArgument(m),
        DriverCallError::DeadlineExceeded(m) => DriverError::DeadlineExceeded(m),
        DriverCallError::NotFound(m) => DriverError::NotFound(m),
        DriverCallError::Unknown(m) => DriverError::Unknown(m),
    }
}

/// A connection to an exporter via its `JUMPSTARTER_HOST` transport socket — the Python
/// driver clients / `j` call these instead of grpcio stubs.
#[derive(uniffi::Object)]
pub struct ClientSession {
    inner: jumpstarter_core::ClientSession,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientSession {
    /// Connect to the lease holder's transport socket.
    #[uniffi::constructor]
    pub async fn connect(host: String) -> Result<Arc<Self>, DriverError> {
        let inner = jumpstarter_core::ClientSession::connect(host)
            .await
            .map_err(from_core_err)?;
        Ok(Arc::new(Self { inner }))
    }

    /// GetReport as a JSON array of driver nodes (the client builds its graph from this).
    pub async fn get_report(&self) -> Result<String, DriverError> {
        self.inner.get_report().await.map_err(from_core_err)
    }

    pub async fn driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<String, DriverError> {
        self.inner
            .driver_call(uuid, method_name, args_json)
            .await
            .map_err(from_core_err)
    }

    pub async fn streaming_driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<Arc<ClientResultStream>, DriverError> {
        let inner = self
            .inner
            .streaming_driver_call(uuid, method_name, args_json)
            .await
            .map_err(from_core_err)?;
        Ok(Arc::new(ClientResultStream { inner }))
    }

    /// Open a router byte stream (driver `@exportstream` / resource handle).
    pub async fn stream(&self, request_json: String) -> Result<Arc<ClientByteStream>, DriverError> {
        let inner = self.inner.stream(request_json).await.map_err(from_core_err)?;
        Ok(Arc::new(ClientByteStream { inner }))
    }

    pub async fn end_session(&self) -> Result<bool, DriverError> {
        self.inner.end_session().await.map_err(from_core_err)
    }

    /// GetStatus as JSON `{status, message, status_version, previous_status}`.
    pub async fn get_status(&self) -> Result<String, DriverError> {
        self.inner.get_status().await.map_err(from_core_err)
    }

    /// Open the exporter LogStream.
    pub async fn log_stream(&self) -> Result<Arc<ClientLogStream>, DriverError> {
        let inner = self.inner.log_stream().await.map_err(from_core_err)?;
        Ok(Arc::new(ClientLogStream { inner }))
    }
}

/// A LogStream of hook + driver/system log entries, pulled JSON-at-a-time.
#[derive(uniffi::Object)]
pub struct ClientLogStream {
    inner: Arc<jumpstarter_core::ClientLogStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientLogStream {
    pub async fn next(&self) -> Result<Option<String>, DriverError> {
        self.inner.next().await.map_err(from_core_err)
    }
}

/// A bidirectional router byte stream (driver `@exportstream` / resource).
#[derive(uniffi::Object)]
pub struct ClientByteStream {
    inner: Arc<jumpstarter_core::ClientByteStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientByteStream {
    /// The resource initial metadata as a JSON object.
    pub fn initial_metadata(&self) -> String {
        self.inner.initial_metadata()
    }

    pub async fn read(&self) -> Result<Option<Vec<u8>>, DriverError> {
        self.inner.read().await.map_err(from_core_err)
    }

    pub async fn write(&self, data: Vec<u8>) -> Result<(), DriverError> {
        self.inner.write(data).await.map_err(from_core_err)
    }

    pub async fn close(&self) -> Result<(), DriverError> {
        self.inner.close().await.map_err(from_core_err)
    }
}

/// A streaming-driver-call result stream, pulled JSON-at-a-time.
#[derive(uniffi::Object)]
pub struct ClientResultStream {
    inner: Arc<jumpstarter_core::ClientResultStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientResultStream {
    pub async fn next(&self) -> Result<Option<String>, DriverError> {
        self.inner.next().await.map_err(from_core_err)
    }
}

/// A `ForeignByteChannel` backed by a `(host, handle)` pair — drives `stream_*`.
struct HandleByteChannel {
    host: Arc<dyn DriverHost>,
    handle: u64,
}

#[async_trait]
impl ForeignByteChannel for HandleByteChannel {
    async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
        self.host.stream_read(self.handle).await.map_err(to_core_err)
    }
    async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
        self.host
            .stream_write(self.handle, data)
            .await
            .map_err(to_core_err)
    }
    async fn close_write(&self) -> Result<(), DriverCallError> {
        self.host
            .stream_close_write(self.handle)
            .await
            .map_err(to_core_err)
    }
    async fn close(&self) -> Result<(), DriverCallError> {
        self.host.stream_close(self.handle).await.map_err(to_core_err)
    }
}
