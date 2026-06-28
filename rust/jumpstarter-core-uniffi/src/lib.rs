//! UniFFI bindings for the Jumpstarter Rust core — the `jumpstarter_core` Python
//! extension (and, from the same definition, future Kotlin/Swift).
//!
//! Thin per-binding layer over [`jumpstarter_driver_core`]: declares the foreign [`DriverHost`]
//! trait Python implements and the exported [`run_exporter`] entry Python awaits, then
//! adapts them to the binding-agnostic `jumpstarter_driver_core::{DriverApi, ...}` and the
//! exporter's `HostFactory`. No business logic here — codec/report/framing live in
//! `jumpstarter-driver-core` / `jumpstarter-codec`.
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

/// Install the process-default rustls `CryptoProvider` (ring) once. The native `jmp` binary does
/// this at startup (`jumpstarter_cli::init_tracing`); the FFI extension is loaded into a host
/// process (Python) that doesn't, so every FFI entry point that opens a TLS connection
/// (controller/exporter/client) must ensure it first — otherwise rustls panics with
/// "Could not automatically determine the process-level CryptoProvider".
fn ensure_crypto_provider() {
    use std::sync::Once;
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

use async_trait::async_trait;
use jumpstarter_codec::error::DriverCallError;
use jumpstarter_driver_core::foreign::ForeignNativeUnary;
use jumpstarter_driver_core::{
    DriverApi, DriverByteChannel, DriverResultStream, DriverStreamOpen, ForeignDriver,
};
use jumpstarter_exporter::backend::{DriverBackend, HostFactory, HostGuard};

mod bytebuf;
use bytebuf::Bytes;

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

// ---------------------------------------------------------------------------
// Resource-stream codec (FFI)
//
// The real exporter decompresses resource uplinks / compresses downlinks in Rust at the host seam
// (`foreign.rs`). The in-process `serve()` path (Python `LocalSession`) has no such Rust seam, so it
// drives these same Rust codecs over FFI — so a compressed resource flashed under `serve()` reaches
// the driver as RAW bytes, exactly like production, with one codec implementation shared by all
// languages. Stateful (streaming): feed chunks, then call `finish()` once at EOF.
// ---------------------------------------------------------------------------

/// Error from a streaming codec.
#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum CodecError {
    #[error("unsupported codec: {0}")]
    Unsupported(String),
    #[error("codec stream already finished")]
    Finished,
    #[error("codec error: {0}")]
    Io(String),
}

/// A streaming compressor for one resource codec (`gzip`/`xz`/`bz2`/`zstd`).
#[derive(uniffi::Object)]
pub struct StreamCompressor {
    inner: std::sync::Mutex<Option<jumpstarter_compression::Compressor>>,
}

#[uniffi::export]
impl StreamCompressor {
    /// Build a compressor for the wire codec string, or `Unsupported` if unrecognized.
    #[uniffi::constructor]
    pub fn new(codec: String) -> Result<Arc<Self>, CodecError> {
        let c = jumpstarter_compression::Codec::from_wire(&codec)
            .ok_or(CodecError::Unsupported(codec))?;
        Ok(Arc::new(Self {
            inner: std::sync::Mutex::new(Some(jumpstarter_compression::Compressor::new(c))),
        }))
    }

    /// Compress one chunk; returns whatever compressed bytes are now available (may be empty).
    pub fn compress(&self, chunk: Bytes) -> Result<Bytes, CodecError> {
        let mut guard = self.inner.lock().expect("codec mutex poisoned");
        let c = guard.as_mut().ok_or(CodecError::Finished)?;
        c.compress(&chunk.0)
            .map(Bytes::from)
            .map_err(|e| CodecError::Io(e.to_string()))
    }

    /// Emit the terminal footer; the compressor is consumed (further calls return `Finished`).
    pub fn finish(&self) -> Result<Bytes, CodecError> {
        let mut guard = self.inner.lock().expect("codec mutex poisoned");
        let c = guard.take().ok_or(CodecError::Finished)?;
        c.finish()
            .map(Bytes::from)
            .map_err(|e| CodecError::Io(e.to_string()))
    }
}

/// A streaming decompressor for one resource codec (`gzip`/`xz`/`bz2`/`zstd`).
#[derive(uniffi::Object)]
pub struct StreamDecompressor {
    inner: std::sync::Mutex<Option<jumpstarter_compression::Decompressor>>,
}

#[uniffi::export]
impl StreamDecompressor {
    /// Build a decompressor for the wire codec string, or `Unsupported` if unrecognized.
    #[uniffi::constructor]
    pub fn new(codec: String) -> Result<Arc<Self>, CodecError> {
        let c = jumpstarter_compression::Codec::from_wire(&codec)
            .ok_or(CodecError::Unsupported(codec))?;
        Ok(Arc::new(Self {
            inner: std::sync::Mutex::new(Some(jumpstarter_compression::Decompressor::new(c))),
        }))
    }

    /// Decompress one chunk; returns whatever decompressed bytes are now available (may be empty).
    /// A malformed stream surfaces a clean `CodecError::Io` (never aborts the process).
    pub fn decompress(&self, chunk: Bytes) -> Result<Bytes, CodecError> {
        let mut guard = self.inner.lock().expect("codec mutex poisoned");
        let d = guard.as_mut().ok_or(CodecError::Finished)?;
        d.decompress(&chunk.0)
            .map(Bytes::from)
            .map_err(|e| CodecError::Io(e.to_string()))
    }

    /// Surface any trailing decompressed bytes at EOF; the decompressor is consumed.
    pub fn finish(&self) -> Result<Bytes, CodecError> {
        let mut guard = self.inner.lock().expect("codec mutex poisoned");
        let d = guard.take().ok_or(CodecError::Finished)?;
        d.finish()
            .map(Bytes::from)
            .map_err(|e| CodecError::Io(e.to_string()))
    }
}

/// A flat driver-tree node (one per `@export` driver instance).
#[derive(uniffi::Record)]
pub struct DriverNode {
    pub uuid: String,
    pub parent_uuid: Option<String>,
    pub labels: HashMap<String, String>,
    pub description: Option<String>,
    pub methods_description: HashMap<String, String>,
    /// Serialized, self-contained `FileDescriptorSet` for this driver's interface (from the host's
    /// `descriptor_builder` introspection): the interface file plus its transitive well-known-type
    /// dependency files (e.g. `google/protobuf/empty.proto`), deps-first. Enables on-demand native
    /// gRPC service. `None` = no native surface for this driver.
    pub descriptor_set: Option<Vec<u8>>,
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

    /// Opaque **native** per-driver unary call — the server-side mirror of the client
    /// [`ClientSession::native_unary`]. A proto-first host (a generated Kotlin `PowerBackend`)
    /// implements its interface as a real gRPC service and decodes/dispatches/encodes the
    /// per-driver proto itself: `path` is the full method path
    /// (`/jumpstarter.interfaces.power.v1.PowerInterface/On`), `body` the encoded request message,
    /// and the return is the encoded response message. `uuid` is the target driver instance.
    ///
    /// A JSON-only host (today's Python host) implements this to raise
    /// [`DriverError::Unimplemented`]; the core then falls back to its descriptor-driven
    /// `driver_call` dispatch, so the legacy path is unaffected. (UniFFI does not permit a Rust
    /// default body on an exported foreign-trait method, so every host declares it explicitly.)
    async fn forward_unary(
        &self,
        uuid: String,
        path: String,
        body: Bytes,
    ) -> Result<Bytes, DriverError>;

    /// Opaque **native** per-driver **server-streaming** call — the streaming mirror of
    /// [`forward_unary`](DriverHost::forward_unary), and the server-side counterpart of the client
    /// `ClientSession::native_server_stream`. The host drives its gRPC service's server-streaming
    /// method (`Read`) and returns a handle the core pulls encoded response messages from via
    /// [`forward_stream_next`](DriverHost::forward_stream_next). A JSON-only host (or one with no
    /// server-streaming surface for `path`) raises [`DriverError::Unimplemented`]; the core then
    /// falls back to its descriptor-driven streaming dispatch.
    async fn forward_server_stream(
        &self,
        uuid: String,
        path: String,
        body: Bytes,
    ) -> Result<u64, DriverError>;
    /// Next encoded response message for a [`forward_server_stream`](DriverHost::forward_server_stream)
    /// handle, or `None` at end of stream. `Bytes` (not `Vec<u8>`) so the byte plane crosses the FFI
    /// in bulk (see [`stream_read`](DriverHost::stream_read)).
    async fn forward_stream_next(&self, handle: u64) -> Result<Option<Bytes>, DriverError>;
    /// Release a [`forward_server_stream`](DriverHost::forward_server_stream) handle.
    async fn forward_stream_close(&self, handle: u64) -> Result<(), DriverError>;

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
    /// Next inbound payload for the channel, or `None` at EOF. `Bytes` (not `Vec<u8>`)
    /// so the byte plane crosses the FFI in bulk, not one bounds-checked byte at a time.
    async fn stream_read(&self, handle: u64) -> Result<Option<Bytes>, DriverError>;
    /// Write one payload toward the driver (bulk-marshalled — see `stream_read`).
    async fn stream_write(&self, handle: u64, data: Bytes) -> Result<(), DriverError>;
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

/// Why [`run_exporter`] returned — the Python host uses this to decide whether to restart the
/// exporter (`Completed`) or terminate the process (`Shutdown`, e.g. an `on_failure: exit` hook).
#[derive(uniffi::Enum)]
pub enum ExporterExit {
    /// Terminate, do NOT restart (a shutdown signal or an `on_failure: exit` hook).
    Shutdown,
    /// Restartable (the serve loop returned on its own, e.g. the controller stream ended).
    Completed,
}

/// Run the exporter in-process against a Python `DriverHostFactory`. Python awaits this on
/// its event loop for the process lifetime; the whole Rust runtime (controller gRPC,
/// session server, lease FSM, hooks) runs inside the call, calling back into the foreign
/// host for driver dispatch.
#[uniffi::export(async_runtime = "tokio")]
pub async fn run_exporter(
    config_path: String,
    factory: Arc<dyn DriverHostFactory>,
) -> Result<ExporterExit, ExporterError> {
    ensure_crypto_provider();
    use jumpstarter_config::{ExporterConfig, YamlConfig};

    let config = ExporterConfig::load(&config_path)
        .map_err(|e| ExporterError::Config(format!("loading exporter config: {e}")))?;
    let host_factory: Arc<dyn HostFactory> = Arc::new(UniffiHostFactory { inner: factory });
    let exit = jumpstarter_exporter::run_with_factory(config, host_factory)
        .await
        .map_err(|e| ExporterError::Runtime(e.to_string()))?;
    let exit = match exit {
        jumpstarter_exporter::ExporterExit::Shutdown => ExporterExit::Shutdown,
        jumpstarter_exporter::ExporterExit::Completed => ExporterExit::Completed,
    };
    let reason = match &exit {
        ExporterExit::Shutdown => "shutdown",
        ExporterExit::Completed => "completed",
    };
    tracing::info!(reason, "run_exporter returned");
    Ok(exit)
}

/// Run the exporter in standalone (controller-less) mode: serve the driver tree directly on
/// `bind` (`host:port`, plaintext h2c) until a termination signal or a client `EndSession`,
/// requiring `passphrase` from clients when set. Runs the before/afterLease hooks once. The
/// driver host is the same in-process foreign host as [`run_exporter`].
/// Install a stderr tracing subscriber (honoring `RUST_LOG`, default `info`) so the
/// standalone exporter's hook output reaches its own stderr — once a client disconnects
/// (e.g. on shutdown) there is no LogStream consumer, so stderr is the only place the
/// afterLease hook output can surface. Idempotent.
fn init_exporter_tracing() {
    use tracing_subscriber::EnvFilter;
    let _ = tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with_writer(std::io::stderr)
        .try_init();
}

#[uniffi::export(async_runtime = "tokio")]
pub async fn run_exporter_standalone(
    config_path: String,
    bind: String,
    passphrase: Option<String>,
    factory: Arc<dyn DriverHostFactory>,
) -> Result<(), ExporterError> {
    ensure_crypto_provider();
    init_exporter_tracing();
    let addr: std::net::SocketAddr = bind
        .parse()
        .map_err(|e| ExporterError::Config(format!("invalid listener bind '{bind}': {e}")))?;
    let host_factory: Arc<dyn HostFactory> = Arc::new(UniffiHostFactory { inner: factory });
    jumpstarter_exporter::serve_standalone_tcp(
        std::path::Path::new(&config_path),
        addr,
        passphrase,
        host_factory,
    )
    .await
    .map_err(|e| ExporterError::Runtime(e.to_string()))
}

/// Serve **one** driver host on a private `uds` for the polyglot hub: provision the foreign
/// driver tree (the single-entry `factory`), then serve the driver-level
/// `ExporterService`+`RouterService` on the socket until the process is killed. No controller,
/// lease, or hooks — the hub owns those. Each per-entry `jumpstarter_exporter_host` subprocess
/// awaits this; the hub dials the socket and federates the entries.
#[uniffi::export(async_runtime = "tokio")]
pub async fn serve_driver_host(
    uds: String,
    factory: Arc<dyn DriverHostFactory>,
) -> Result<(), ExporterError> {
    init_exporter_tracing();
    // NOTE: the parent-death watchdog for a *Python* host lives in the Python host itself
    // (`jumpstarter.exporter_host`), not here: terminating a Python process from this embedded
    // Rust core is fragile (CPython finalization deadlocks on exit). The native `jmp-rust-host`
    // — a real Rust process — uses `jumpstarter_exporter::exit_when_orphaned()` directly.
    let host_factory: Arc<dyn HostFactory> = Arc::new(UniffiHostFactory { inner: factory });
    let (backend, _guard) = host_factory
        .provision()
        .await
        .map_err(|e| ExporterError::Runtime(e.to_string()))?;

    // The shared host-SDK entrypoint builds the routing table, pins the session, and serves the
    // driver-host seam on the UDS until the host process is killed (the same helper the native
    // `jmp-rust-host` and per-crate native hosts use).
    jumpstarter_exporter::session::serve_native_host(std::path::Path::new(&uds), backend)
        .await
        .map_err(|e| ExporterError::Runtime(e.to_string()))?;
    drop(_guard);
    Ok(())
}

/// Run the exporter as the **polyglot hub**: register with the controller and, per lease, spawn
/// one driver host per top-level `export:` entry (a Python `jumpstarter_exporter_host`
/// subprocess today; native Rust later), federating them by UUID. The hub embeds no language
/// runtime — a pure-native driver set spawns no Python at all. Python awaits this for the
/// process lifetime; returns why it ended (restart vs terminate), like [`run_exporter`].
#[uniffi::export(async_runtime = "tokio")]
pub async fn run_exporter_polyglot(config_path: String) -> Result<ExporterExit, ExporterError> {
    ensure_crypto_provider();
    use jumpstarter_config::{ExporterConfig, YamlConfig};

    init_exporter_tracing();
    let config = ExporterConfig::load(&config_path)
        .map_err(|e| ExporterError::Config(format!("loading exporter config: {e}")))?;
    let factory: Arc<dyn HostFactory> = Arc::new(
        jumpstarter_exporter::polyglot::PolyglotHostFactory::new(std::path::PathBuf::from(
            config_path,
        )),
    );
    let exit = jumpstarter_exporter::run_with_factory(config, factory)
        .await
        .map_err(|e| ExporterError::Runtime(e.to_string()))?;
    Ok(match exit {
        jumpstarter_exporter::ExporterExit::Shutdown => ExporterExit::Shutdown,
        jumpstarter_exporter::ExporterExit::Completed => ExporterExit::Completed,
    })
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

fn to_core_node(n: DriverNode) -> jumpstarter_codec::dto::DriverNode {
    jumpstarter_codec::dto::DriverNode {
        uuid: n.uuid,
        parent_uuid: n.parent_uuid,
        labels: n.labels,
        description: n.description,
        methods_description: n.methods_description,
        descriptor_set: n.descriptor_set,
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
    ) -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), jumpstarter_exporter::Error> {
        let host = self
            .inner
            .new_host()
            .map_err(|e| jumpstarter_exporter::Error::Config(format!("new_host failed: {e}")))?;
        let api: Arc<dyn DriverApi> = Arc::new(UniffiHostApi {
            inner: host.clone(),
        });
        // Attach the proto-first server seam: a host that implements its interface as a real gRPC
        // service (a generated Kotlin `PowerBackend`) serves native unary calls through
        // `DriverHost::forward_unary` directly. A JSON-only host raises `Unimplemented` there, so
        // the core transparently falls back to its descriptor-driven `driver_call` dispatch.
        let native_unary: Arc<dyn ForeignNativeUnary> = Arc::new(UniffiNativeUnary {
            inner: host.clone(),
        });
        let foreign = ForeignDriver::new(api).with_native_unary(native_unary);
        // Introspect the driver tree NOW (at host startup) so the native gRPC interface is instantly
        // ready to serve — no first-call latency, and any descriptor problems surface here. Failure
        // is non-fatal (the native side is best-effort; legacy dispatch is unaffected).
        if let Err(e) = foreign.prepare().await {
            tracing::warn!(error = %e, "native interface introspection failed at startup");
        }
        let backend: Arc<dyn DriverBackend> = Arc::new(foreign);
        // Hold the DriverHost Arc as the lease guard so the foreign tree's lifetime is
        // explicit (dropped at lease end alongside the backend).
        Ok((backend, Box::new(host)))
    }
}

/// Wraps a foreign `DriverHost` as `jumpstarter_driver_core::DriverApi`.
struct UniffiHostApi {
    inner: Arc<dyn DriverHost>,
}

/// Wraps a foreign `DriverHost` as the proto-first [`ForeignNativeUnary`] server seam, bridging the
/// core's opaque `forward_unary` to the host's own gRPC service (`DriverHost::forward_unary`). A
/// JSON-only host's `forward_unary` raises `Unimplemented`, which the `ForeignDriver` treats as
/// "decline" and falls back to the descriptor/JSON dispatch.
struct UniffiNativeUnary {
    inner: Arc<dyn DriverHost>,
}

#[async_trait]
impl ForeignNativeUnary for UniffiNativeUnary {
    async fn forward_unary(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Vec<u8>, DriverCallError> {
        self.inner
            .forward_unary(uuid, path, Bytes(body))
            .await
            .map(Vec::from)
            .map_err(to_core_err)
    }

    async fn forward_server_stream(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Arc<dyn jumpstarter_driver_core::foreign::ForeignNativeByteStream>, DriverCallError> {
        let handle = self
            .inner
            .forward_server_stream(uuid, path, Bytes(body))
            .await
            .map_err(to_core_err)?;
        Ok(Arc::new(HandleNativeByteStream {
            host: self.inner.clone(),
            handle,
        }))
    }
}

/// A [`ForeignNativeByteStream`] backed by a `(host, handle)` pair — drives the host's handle-based
/// `forward_stream_next`/`forward_stream_close`, releasing the handle at end of stream (mirrors
/// [`HandleResultStream`]).
struct HandleNativeByteStream {
    host: Arc<dyn DriverHost>,
    handle: u64,
}

#[async_trait]
impl jumpstarter_driver_core::foreign::ForeignNativeByteStream for HandleNativeByteStream {
    async fn next(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
        let item = self
            .inner_next()
            .await
            .map(|opt| opt.map(Vec::from))
            .map_err(to_core_err)?;
        if item.is_none() {
            // End of stream — release the handle (best effort).
            let _ = self.host.forward_stream_close(self.handle).await;
        }
        Ok(item)
    }
}

impl HandleNativeByteStream {
    async fn inner_next(&self) -> Result<Option<Bytes>, DriverError> {
        self.host.forward_stream_next(self.handle).await
    }
}

#[async_trait]
impl DriverApi for UniffiHostApi {
    async fn describe(&self) -> Result<Vec<jumpstarter_codec::dto::DriverNode>, DriverCallError> {
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
    ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
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

    async fn open_stream(&self, request_json: String) -> Result<DriverStreamOpen, DriverCallError> {
        let opened = self.inner.open_stream(request_json).await.map_err(to_core_err)?;
        let channel: Arc<dyn DriverByteChannel> = Arc::new(HandleByteChannel {
            host: self.inner.clone(),
            handle: opened.handle,
        });
        let initial_metadata = opened
            .initial_metadata
            .into_iter()
            .map(|e| (e.key, e.value))
            .collect();
        Ok(DriverStreamOpen {
            channel,
            initial_metadata,
        })
    }
}

/// A `DriverResultStream` backed by a `(host, handle)` pair — drives `streaming_next`.
struct HandleResultStream {
    host: Arc<dyn DriverHost>,
    handle: u64,
}

#[async_trait]
impl DriverResultStream for HandleResultStream {
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
    inner: jumpstarter_client::ClientSession,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientSession {
    /// Connect to the lease holder's transport socket.
    #[uniffi::constructor]
    pub async fn connect(host: String) -> Result<Arc<Self>, DriverError> {
        ensure_crypto_provider();
        let inner = jumpstarter_client::ClientSession::connect(host)
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

    /// Opaque **native** per-driver unary gRPC call — the client side of the native calls surface.
    /// `path` is the full method path (`/jumpstarter.driver.power.v1.PowerInterface/On`), `body` the
    /// encoded request message; the Python custom gRPC channel serializes the stock `protoc` stub's
    /// request into `body` and feeds the response bytes back to the stub. The `uuid` rides as the
    /// `x-jumpstarter-driver-uuid` header so the exporter demux routes to the right instance.
    pub async fn native_unary(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Vec<u8>, DriverError> {
        self.inner
            .native_unary(uuid, path, body)
            .await
            .map_err(from_core_err)
    }

    /// Opaque **native** per-driver **server-streaming** gRPC call — the streaming half of the native
    /// calls surface, the counterpart of [`Self::native_unary`]. Returns a [`ClientNativeStream`] the
    /// foreign stub's custom channel pulls message-at-a-time, decoding each with its own response
    /// marshaller; the core never sees the per-driver proto. `uuid` rides the `x-jumpstarter-driver-uuid`
    /// header so the exporter demux routes to the right instance.
    pub async fn native_server_stream(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Arc<ClientNativeStream>, DriverError> {
        let inner = self
            .inner
            .open_native_server_stream(uuid, path, body)
            .await
            .map_err(from_core_err)?;
        Ok(Arc::new(ClientNativeStream { inner }))
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
    inner: Arc<jumpstarter_client::ClientLogStream>,
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
    inner: Arc<jumpstarter_client::ClientByteStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientByteStream {
    /// The resource initial metadata as a JSON object.
    pub fn initial_metadata(&self) -> String {
        self.inner.initial_metadata()
    }

    pub async fn read(&self) -> Result<Option<Bytes>, DriverError> {
        self.inner
            .read()
            .await
            .map(|opt| opt.map(Bytes))
            .map_err(from_core_err)
    }

    pub async fn write(&self, data: Bytes) -> Result<(), DriverError> {
        self.inner.write(data.0).await.map_err(from_core_err)
    }

    /// Gracefully end the byte stream (half-close the send side / END_STREAM). Named `shutdown`, not
    /// `close`, because UniFFI's Kotlin backend makes every object `AutoCloseable` with a `close()`
    /// handle-disposer — an exported async `close()` would be a conflicting overload there.
    pub async fn shutdown(&self) -> Result<(), DriverError> {
        self.inner.close().await.map_err(from_core_err)
    }
}

/// A streaming-driver-call result stream, pulled JSON-at-a-time.
#[derive(uniffi::Object)]
pub struct ClientResultStream {
    inner: Arc<jumpstarter_client::ClientResultStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientResultStream {
    pub async fn next(&self) -> Result<Option<String>, DriverError> {
        self.inner.next().await.map_err(from_core_err)
    }
}

/// An opaque **native** server-streaming response, pulled message-at-a-time as raw proto bytes —
/// the streaming counterpart of [`ClientSession::native_unary`]. The foreign gRPC stub's custom
/// channel decodes each message with its own response marshaller; `None` is the clean end of stream.
#[derive(uniffi::Object)]
pub struct ClientNativeStream {
    inner: Arc<jumpstarter_client::ClientNativeStream>,
}

#[uniffi::export(async_runtime = "tokio")]
impl ClientNativeStream {
    pub async fn next(&self) -> Result<Option<Vec<u8>>, DriverError> {
        self.inner.next().await.map_err(from_core_err)
    }
}

/// A `DriverByteChannel` backed by a `(host, handle)` pair — drives `stream_*`.
struct HandleByteChannel {
    host: Arc<dyn DriverHost>,
    handle: u64,
}

#[async_trait]
impl DriverByteChannel for HandleByteChannel {
    async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
        self.host
            .stream_read(self.handle)
            .await
            .map(|opt| opt.map(Vec::from))
            .map_err(to_core_err)
    }
    async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
        self.host
            .stream_write(self.handle, Bytes(data))
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

// ---------------------------------------------------------------------------
// Controller/lease side: the programmatic lease surface (jumpstarter-testing / MCP)
// ---------------------------------------------------------------------------

/// A controller/lease operation failure surfaced to the language binding.
#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum ControllerError {
    #[error("config error: {0}")]
    Config(String),
    #[error("connection error: {0}")]
    Connection(String),
    #[error("unsatisfiable: {0}")]
    Unsatisfiable(String),
    #[error("timeout: {0}")]
    Timeout(String),
    #[error("{0}")]
    Other(String),
}

fn from_core_controller_err(e: jumpstarter_codec::ControllerError) -> ControllerError {
    use jumpstarter_codec::ControllerError as C;
    match e {
        C::Config(m) => ControllerError::Config(m),
        C::Connection(m) => ControllerError::Connection(m),
        C::Unsatisfiable(m) => ControllerError::Unsatisfiable(m),
        C::Timeout(m) => ControllerError::Timeout(m),
        C::Other(m) => ControllerError::Other(m),
    }
}

/// The result of acquiring a lease: the lease name and the assigned exporter name.
#[derive(uniffi::Record)]
pub struct AcquiredLease {
    pub name: String,
    pub exporter: String,
}

/// A connected controller session (the language's `Lease` shim drives lease ops through this).
#[derive(uniffi::Object)]
pub struct ControllerSession {
    inner: jumpstarter_client::ControllerSession,
}

#[uniffi::export(async_runtime = "tokio")]
impl ControllerSession {
    /// Connect + authenticate to the controller from explicit connection fields.
    #[uniffi::constructor]
    pub async fn connect(
        endpoint: String,
        token: Option<String>,
        ca: String,
        tls_insecure: bool,
        namespace: String,
        name: String,
    ) -> Result<Arc<Self>, ControllerError> {
        ensure_crypto_provider();
        let inner = jumpstarter_client::ControllerSession::connect(
            endpoint, token, ca, tls_insecure, namespace, name,
        )
        .await
        .map_err(from_core_controller_err)?;
        Ok(Arc::new(Self { inner }))
    }

    /// Acquire a lease (full FSM); returns the lease name + assigned exporter.
    pub async fn acquire_lease(
        &self,
        selector: Option<String>,
        exporter_name: Option<String>,
        existing_name: Option<String>,
        duration_secs: u64,
        acquisition_timeout_secs: u64,
    ) -> Result<AcquiredLease, ControllerError> {
        let acquired = self
            .inner
            .acquire_lease(
                selector,
                exporter_name,
                existing_name,
                duration_secs,
                acquisition_timeout_secs,
            )
            .await
            .map_err(from_core_controller_err)?;
        Ok(AcquiredLease {
            name: acquired.name,
            exporter: acquired.exporter,
        })
    }

    /// Release (delete) a lease by name.
    pub async fn release_lease(&self, name: String) -> Result<(), ControllerError> {
        self.inner.release_lease(name).await.map_err(from_core_controller_err)
    }

    /// Start a transport listener for the lease; the returned handle exposes the
    /// `JUMPSTARTER_HOST` socket path the language client connects to.
    pub async fn serve_lease(&self, name: String) -> Result<Arc<LeaseTransport>, ControllerError> {
        let inner = self.inner.serve_lease(name).await.map_err(from_core_controller_err)?;
        Ok(Arc::new(LeaseTransport { inner }))
    }

    /// List exporters as a JSON array string (`filter` = label selector). Each entry:
    /// `{name, labels, online, status}`.
    pub async fn list_exporters(&self, filter: Option<String>) -> Result<String, ControllerError> {
        self.inner.list_exporters_json(filter).await.map_err(from_core_controller_err)
    }

    /// List leases as a JSON array string. Each entry: `{name, client, exporter, selector,
    /// exporter_name, tags, conditions, begin_time_epoch, end_time_epoch, duration_seconds}`.
    pub async fn list_leases(
        &self,
        filter: Option<String>,
        only_active: bool,
        tag_filter: Option<String>,
    ) -> Result<String, ControllerError> {
        self.inner
            .list_leases_json(filter, only_active, tag_filter)
            .await
            .map_err(from_core_controller_err)
    }

    /// Create a lease (does not wait for Ready); returns the created lease name.
    pub async fn create_lease(
        &self,
        duration_secs: u64,
        selector: Option<String>,
        exporter_name: Option<String>,
        tags: HashMap<String, String>,
    ) -> Result<String, ControllerError> {
        self.inner
            .create_lease(duration_secs, selector, exporter_name, tags.into_iter().collect())
            .await
            .map_err(from_core_controller_err)
    }
}

/// A fully self-managed leased exporter: resolve a client config, acquire a lease, and serve
/// it on a local transport in one call. This is the **auto-acquire-lease** capability shared
/// by every language test runtime — Python/Kotlin/Swift consume this uniffi object directly
/// (a future C binding wraps the same core composition), so test authors get "give me a
/// leased exporter from my config" without re-implementing the lease lifecycle per language.
/// `release()` tears down the transport and releases the lease.
#[derive(uniffi::Object)]
pub struct LeasedExporter {
    session: jumpstarter_client::ControllerSession,
    // Held so the listener stays up for the lease's lifetime; closed by `release`.
    _transport: Arc<jumpstarter_client::LeaseTransport>,
    name: String,
    host: String,
    exporter: String,
    allow: Vec<String>,
    unsafe_drivers: bool,
}

#[uniffi::export(async_runtime = "tokio")]
impl LeasedExporter {
    /// Resolve the client config at `config_path`, acquire a lease (optionally constrained by
    /// `selector`/`exporter_name`, or joining `existing_name`), and serve it on a local socket.
    #[uniffi::constructor]
    pub async fn acquire(
        config_path: String,
        selector: Option<String>,
        exporter_name: Option<String>,
        existing_name: Option<String>,
        duration_secs: u64,
    ) -> Result<Arc<Self>, ControllerError> {
        ensure_crypto_provider();
        use jumpstarter_config::{ClientConfig, YamlConfig};
        let cfg = ClientConfig::load(&config_path).map_err(|e| ControllerError::Config(e.to_string()))?;
        let session = jumpstarter_client::ControllerSession::connect(
            cfg.endpoint.clone().unwrap_or_default(),
            cfg.token.clone(),
            cfg.tls.ca.clone(),
            cfg.tls.insecure,
            cfg.metadata.namespace.clone().unwrap_or_else(|| "default".to_string()),
            cfg.metadata.name.clone(),
        )
        .await
        .map_err(from_core_controller_err)?;
        let acquired = session
            .acquire_lease(
                selector,
                exporter_name,
                existing_name,
                duration_secs,
                cfg.leases.acquisition_timeout as u64,
            )
            .await
            .map_err(from_core_controller_err)?;
        let transport = session.serve_lease(acquired.name.clone()).await.map_err(from_core_controller_err)?;
        let host = transport.jumpstarter_host().await.map_err(from_core_controller_err)?;
        // Preserve the `"UNSAFE" in allow` sentinel so the client tree's driver-allow policy matches.
        let unsafe_drivers = cfg.drivers.r#unsafe || cfg.drivers.allow.iter().any(|d| d == "UNSAFE");
        Ok(Arc::new(Self {
            session,
            _transport: transport,
            name: acquired.name,
            host,
            exporter: acquired.exporter,
            allow: cfg.drivers.allow.clone(),
            unsafe_drivers,
        }))
    }

    /// The `JUMPSTARTER_HOST` socket path the language client connects to.
    pub fn jumpstarter_host(&self) -> String {
        self.host.clone()
    }

    /// The assigned exporter name.
    pub fn exporter_name(&self) -> String {
        self.exporter.clone()
    }

    /// The driver allow-list + unsafe flag for building the client tree.
    pub fn allow(&self) -> Vec<String> {
        self.allow.clone()
    }
    pub fn unsafe_drivers(&self) -> bool {
        self.unsafe_drivers
    }

    /// Tear down the transport and release the lease. Call once on test teardown.
    pub async fn release(&self) -> Result<(), ControllerError> {
        self._transport.close().await;
        self.session.release_lease(self.name.clone()).await.map_err(from_core_controller_err)
    }
}

/// A live transport listener for one lease (drop/close tears it down).
#[derive(uniffi::Object)]
pub struct LeaseTransport {
    inner: Arc<jumpstarter_client::LeaseTransport>,
}

#[uniffi::export(async_runtime = "tokio")]
impl LeaseTransport {
    /// The Unix socket path to export as `JUMPSTARTER_HOST`.
    pub async fn jumpstarter_host(&self) -> Result<String, ControllerError> {
        self.inner.jumpstarter_host().await.map_err(from_core_controller_err)
    }

    /// Stop the listener + remove the socket (idempotent). Named `shutdown`, not `close`, to avoid
    /// colliding with the `AutoCloseable.close()` handle-disposer UniFFI's Kotlin backend generates.
    pub async fn shutdown(&self) {
        self.inner.close().await;
    }
}

// ---------------------------------------------------------------------------
// Config YAML parsing — so the Python config layer drops pyyaml (parsing in Rust)
// ---------------------------------------------------------------------------

/// A YAML parse/serialize failure.
#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum YamlError {
    #[error("yaml error: {0}")]
    Parse(String),
}

/// Parse a YAML document into a JSON string (the Python config layer then `json.loads` +
/// validates with pydantic). Replaces `yaml.safe_load`.
#[uniffi::export]
pub fn parse_yaml(text: String) -> Result<String, YamlError> {
    let value: serde_yaml_ng::Value =
        serde_yaml_ng::from_str(&text).map_err(|e| YamlError::Parse(e.to_string()))?;
    serde_json::to_string(&value).map_err(|e| YamlError::Parse(e.to_string()))
}

/// Serialize a JSON string to a YAML document, preserving key order (serde_yaml_ng::Value is
/// an order-preserving map, fed from the JSON in document order). Replaces
/// `yaml.safe_dump(..., sort_keys=False)`.
#[uniffi::export]
pub fn dump_yaml(json: String) -> Result<String, YamlError> {
    let value: serde_yaml_ng::Value =
        serde_json::from_str(&json).map_err(|e| YamlError::Parse(e.to_string()))?;
    serde_yaml_ng::to_string(&value).map_err(|e| YamlError::Parse(e.to_string()))
}

// ---------------------------------------------------------------------------
// Config records — so Python stops parsing config YAML (the Rust config crate does it)
// ---------------------------------------------------------------------------

/// One node of the exporter `export` driver tree, flattened from the untagged
/// `DriverInstance` enum. `instantiate()` stays in Python (it imports driver classes by
/// dotted path); this only moves the YAML parsing to Rust.
#[derive(uniffi::Record)]
pub struct DriverSpecNode {
    /// Dotted driver class path (empty for proxy/composite nodes).
    pub r#type: String,
    /// Set for proxy nodes (the YAML `ref`).
    pub reference: Option<String>,
    pub description: Option<String>,
    pub methods_description: HashMap<String, String>,
    /// Driver kwargs as a JSON object string (Python `json.loads` → ctor kwargs).
    pub config_json: String,
    pub children: HashMap<String, DriverSpecNode>,
}

/// The exporter's parsed driver tree (the bit `DriverHostFactory` needs).
#[derive(uniffi::Record)]
pub struct ExporterSpec {
    pub description: Option<String>,
    pub export: HashMap<String, DriverSpecNode>,
}

fn driver_node_to_spec(node: &jumpstarter_config::DriverInstance) -> DriverSpecNode {
    use jumpstarter_config::DriverInstance;
    let children_to_spec = |children: &std::collections::BTreeMap<String, DriverInstance>| {
        children
            .iter()
            .map(|(k, v)| (k.clone(), driver_node_to_spec(v)))
            .collect()
    };
    match node {
        DriverInstance::Base(b) => DriverSpecNode {
            r#type: b.r#type.clone(),
            reference: None,
            description: b.description.clone(),
            methods_description: b.methods_description.clone().into_iter().collect(),
            config_json: serde_json::to_string(&b.config).unwrap_or_else(|_| "{}".to_string()),
            children: children_to_spec(&b.children),
        },
        DriverInstance::Proxy(p) => DriverSpecNode {
            r#type: String::new(),
            reference: Some(p.reference.clone()),
            description: None,
            methods_description: HashMap::new(),
            config_json: "{}".to_string(),
            children: HashMap::new(),
        },
        DriverInstance::Composite(c) => DriverSpecNode {
            r#type: String::new(),
            reference: None,
            description: None,
            methods_description: HashMap::new(),
            config_json: "{}".to_string(),
            children: children_to_spec(&c.children),
        },
    }
}

/// Load an exporter config from disk and return its driver tree (Rust parses the YAML).
#[uniffi::export]
pub fn load_exporter_spec(config_path: String) -> Result<ExporterSpec, YamlError> {
    use jumpstarter_config::{ExporterConfig, YamlConfig};
    let config = ExporterConfig::load(&config_path).map_err(|e| YamlError::Parse(e.to_string()))?;
    Ok(exporter_spec(&config))
}

/// Parse an exporter config from a YAML string and return its driver tree — the polyglot hub
/// hands each per-entry config to its host process directly (no temp files on disk).
#[uniffi::export]
pub fn load_exporter_spec_str(yaml: String) -> Result<ExporterSpec, YamlError> {
    use jumpstarter_config::{ExporterConfig, YamlConfig};
    let config = ExporterConfig::from_yaml(&yaml).map_err(|e| YamlError::Parse(e.to_string()))?;
    Ok(exporter_spec(&config))
}

fn exporter_spec(config: &jumpstarter_config::ExporterConfig) -> ExporterSpec {
    ExporterSpec {
        description: config.description.clone(),
        export: config
            .export
            .iter()
            .map(|(k, v)| (k.clone(), driver_node_to_spec(v)))
            .collect(),
    }
}

/// The connection + driver-policy fields the client lease needs (flat, no pydantic).
#[derive(uniffi::Record)]
pub struct ClientConnectionSpec {
    pub endpoint: Option<String>,
    pub namespace: Option<String>,
    pub name: String,
    pub token: Option<String>,
    pub ca: String,
    pub insecure: bool,
    pub allow: Vec<String>,
    pub r#unsafe: bool,
    pub acquisition_timeout: i64,
}

fn client_connection_from_config(c: &jumpstarter_config::ClientConfig) -> ClientConnectionSpec {
    ClientConnectionSpec {
        endpoint: c.endpoint.clone(),
        namespace: c.metadata.namespace.clone(),
        name: c.metadata.name.clone(),
        token: c.token.clone(),
        ca: c.tls.ca.clone(),
        insecure: c.tls.insecure,
        allow: c.drivers.allow.clone(),
        r#unsafe: c.drivers.r#unsafe,
        acquisition_timeout: c.leases.acquisition_timeout,
    }
}

/// Load a client config from disk and return the lease connection fields (Rust parses YAML).
#[uniffi::export]
pub fn load_client_connection(path: String) -> Result<ClientConnectionSpec, YamlError> {
    use jumpstarter_config::{ClientConfig, YamlConfig};
    let config = ClientConfig::load(&path).map_err(|e| YamlError::Parse(e.to_string()))?;
    Ok(client_connection_from_config(&config))
}

/// The client connection fields from `JMP_*` env vars, if a complete config is present.
#[uniffi::export]
pub fn client_connection_from_env() -> Option<ClientConnectionSpec> {
    jumpstarter_config::client_from_env().map(|c| client_connection_from_config(&c))
}
