//! `ForeignDriver` — the in-process driver host.
//!
//! Adapts a binding-agnostic [`DriverApi`] (implemented in Python/Kotlin/C) to the
//! exporter's proto-typed [`DriverBackend`] seam, so the Rust exporter serves driver
//! calls/streams by calling the foreign host *in process* instead of proxying gRPC to a
//! subprocess. This is the in-process counterpart to the subprocess `ChannelBackend` — same
//! behavior, no second process and no second gRPC stack.
//!
//! Rust owns everything mechanical here: the value codec (`args`/`result` proto `Value`
//! ⇄ JSON via [`crate::codec`]), `DriverReport` assembly (via [`crate::report`]), the
//! exception→`Status` mapping, and the router framing (DATA/GOAWAY + the trailing
//! `ABORTED "RouterStream: aclose"` teardown). The foreign side only runs driver method
//! bodies and produces/consumes raw bytes + JSON.

use std::sync::Arc;

use prost::Message as _;
use prost_reflect::prost_types::{FileDescriptorProto, FileDescriptorSet};
use prost_reflect::DescriptorPool;
use tokio::sync::OnceCell;

use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};

use crate::dynamic_backend::DynamicBackend;
use crate::stream_pump::{downlink_chunk, downlink_finish, uplink_chunk, uplink_finish};
use jumpstarter_protocol::v1::{
    FrameType, GetReportResponse, LogStreamResponse, StreamResponse,
};
use jumpstarter_protocol::{decode_stream_data, encode_stream_data, RESOURCE_OPEN_PATH};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataKey, MetadataMap};
use tonic::Status;

use jumpstarter_compression::{Codec, Compressor, Decompressor};

use crate::error::DriverCallError;
use crate::host::DriverApi;
use crate::report::assemble_report;

/// Mirrors `driver/base.py:27-36`: `JMP_DISABLE_COMPRESSION=1` turns the wire codec off
/// entirely (the host then advertises no accept and runs pure passthrough).
fn compression_disabled() -> bool {
    std::env::var(jumpstarter_config::env::JMP_DISABLE_COMPRESSION).as_deref() == Ok("1")
}

/// Parse the requested wire codec out of an already-materialized `request` stream-open JSON.
///
/// A driver `@exportstream` request carries `method` and no `x_jmp_content_encoding`, so this
/// returns `None` there → pure passthrough (consoles/serial/network are unchanged). Only a
/// resource request with a recognized `x_jmp_content_encoding` (and compression enabled) yields
/// a codec.
fn parse_codec(request_json: &str) -> Option<Codec> {
    if compression_disabled() {
        return None;
    }
    serde_json::from_str::<serde_json::Value>(request_json)
        .ok()
        .and_then(|v| {
            v.get("x_jmp_content_encoding")
                .and_then(|e| e.as_str())
                .map(String::from)
        })
        .and_then(|s| Codec::from_wire(&s))
}

/// Channel buffer for the in-process result/frame pumps. Small: each item is one driver
/// result or one stream frame, and the foreign side is GIL-bounded anyway.
const PUMP_BUFFER: usize = 16;

/// The **opaque server seam** a proto-first foreign host (e.g. a generated Kotlin `PowerBackend`)
/// provides — the server-side mirror of the client `ClientSession::native_unary`. A host that
/// implements its interface as a *real gRPC service* (decoding the prost request, calling the
/// author's typed handler, encoding the response) routes inbound `(path, body)` through this
/// instead of the JSON `driver_call` codec, so the core never decodes the per-driver proto.
///
/// `None`/absent ⇒ [`ForeignDriver`] keeps using its existing descriptor-driven JSON
/// (`driver_call`) dispatch — the two paths coexist so nothing relying on the legacy codec breaks.
#[async_trait::async_trait]
pub trait ForeignNativeUnary: Send + Sync {
    /// Serve one opaque native unary call: `uuid` is the target driver instance (the demux
    /// `x-jumpstarter-driver-uuid` header), `path` the full gRPC method path
    /// (`/jumpstarter.interfaces.power.v1.PowerInterface/On`), `body` the encoded request message;
    /// returns the encoded response message bytes.
    async fn forward_unary(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Vec<u8>, DriverCallError>;

    /// Serve one opaque native **server-streaming** call — the streaming counterpart of
    /// [`forward_unary`](Self::forward_unary). The host drives its gRPC service's server-streaming
    /// method and returns a pull-style stream of encoded response messages (`Read` -> a stream of
    /// encoded `PowerReading`s). A host with no server-streaming surface for `path` declines with
    /// [`DriverCallError::Unimplemented`], and the [`ForeignDriver`] falls back to the legacy
    /// descriptor/JSON streaming dispatch.
    async fn forward_server_stream(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Arc<dyn ForeignNativeByteStream>, DriverCallError>;
}

/// A pull-style stream of opaque encoded response messages for a native server-streaming call
/// (the byte-plane counterpart of [`DriverResultStream`]).
#[async_trait::async_trait]
pub trait ForeignNativeByteStream: Send + Sync {
    /// The next encoded response message, or `None` at end of stream.
    async fn next(&self) -> Result<Option<Vec<u8>>, DriverCallError>;
}

/// Wraps a [`DriverApi`] as a [`DriverBackend`].
pub struct ForeignDriver {
    api: Arc<dyn DriverApi>,
    /// The native-gRPC dispatcher, built lazily from the host's per-driver descriptors on the first
    /// native call (the legacy `driver_call` path doesn't need it).
    native: OnceCell<DynamicBackend>,
    /// Optional proto-first server seam. When present, [`forward_unary`](Self::forward_unary)
    /// routes the opaque native call straight to the foreign host's own gRPC service instead of
    /// decoding it to a JSON `driver_call`. Absent ⇒ the legacy descriptor/JSON path is used.
    native_unary: Option<Arc<dyn ForeignNativeUnary>>,
}

impl ForeignDriver {
    pub fn new(api: Arc<dyn DriverApi>) -> Self {
        Self {
            api,
            native: OnceCell::new(),
            native_unary: None,
        }
    }

    /// Attach a proto-first [`ForeignNativeUnary`] server seam so native unary calls bypass the
    /// JSON `driver_call` codec and reach the foreign host's own gRPC service. The legacy
    /// descriptor/JSON dispatch stays available for everything else (streaming, byte plane, and
    /// any host that does not implement this seam).
    pub fn with_native_unary(mut self, native_unary: Arc<dyn ForeignNativeUnary>) -> Self {
        self.native_unary = Some(native_unary);
        self
    }

    /// Try the proto-first [`ForeignNativeUnary`] server seam for one opaque native call. Returns
    /// `Ok(Some(response_bytes))` when the host served it natively, `Ok(None)` when no seam is
    /// attached or the host declined (`Unimplemented`) — meaning the caller should fall back to the
    /// descriptor-driven `driver_call` dispatch — and `Err` for any other host failure.
    async fn try_native_unary(
        &self,
        path: &str,
        metadata: &MetadataMap,
        body: &bytes::Bytes,
    ) -> Result<Option<bytes::Bytes>, Status> {
        let Some(native_unary) = &self.native_unary else {
            return Ok(None);
        };
        let uuid = metadata
            .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned)
            .ok_or_else(|| {
                Status::invalid_argument("native unary call missing x-jumpstarter-driver-uuid header")
            })?;
        match native_unary
            .forward_unary(uuid, path.to_string(), body.to_vec())
            .await
        {
            Ok(resp) => Ok(Some(bytes::Bytes::from(resp))),
            // Host has no native gRPC surface for this method → fall back to legacy dispatch.
            Err(DriverCallError::Unimplemented(_)) => Ok(None),
            Err(e) => Err(status_from(e)),
        }
    }

    /// Try the proto-first server-streaming seam for one opaque native call. Returns
    /// `Ok(Some(stream))` of encoded response messages when the host served it natively,
    /// `Ok(None)` when no seam is attached or the host declined (`Unimplemented`), and `Err` for
    /// any other host failure. The host's pull-style [`ForeignNativeByteStream`] is pumped into a
    /// [`ResponseStream`] (one task draining it) so it slots into the `forward_stream` return.
    async fn try_native_server_stream(
        &self,
        path: &str,
        metadata: &MetadataMap,
        body: &bytes::Bytes,
    ) -> Result<Option<ResponseStream<bytes::Bytes>>, Status> {
        let Some(native_unary) = &self.native_unary else {
            return Ok(None);
        };
        let uuid = metadata
            .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned)
            .ok_or_else(|| {
                Status::invalid_argument("native unary call missing x-jumpstarter-driver-uuid header")
            })?;
        let foreign_stream = match native_unary
            .forward_server_stream(uuid, path.to_string(), body.to_vec())
            .await
        {
            Ok(stream) => stream,
            // Host has no server-streaming surface for this method → fall back to legacy dispatch.
            Err(DriverCallError::Unimplemented(_)) => return Ok(None),
            Err(e) => return Err(status_from(e)),
        };
        // Drain the host's pull-style stream into a channel-backed `ResponseStream` (the same
        // pump shape the byte plane uses). A host error becomes a non-OK item the client observes.
        let (tx, rx) = mpsc::channel::<Result<bytes::Bytes, Status>>(PUMP_BUFFER);
        tokio::spawn(async move {
            loop {
                match foreign_stream.next().await {
                    Ok(Some(msg)) => {
                        if tx.send(Ok(bytes::Bytes::from(msg))).await.is_err() {
                            break;
                        }
                    }
                    Ok(None) => break,
                    Err(e) => {
                        let _ = tx.send(Err(status_from(e))).await;
                        break;
                    }
                }
            }
        });
        Ok(Some(Box::pin(ReceiverStream::new(rx))))
    }

    /// Eagerly introspect + build the on-demand native dispatcher **at startup**, so the native
    /// interface is instantly ready (no first-call latency, and descriptors are validated up front).
    /// Called once when the host is provisioned; [`forward_unary`](Self::forward_unary) reuses the
    /// cached result. Best-effort: a driver whose descriptor is malformed or has unresolved imports
    /// is logged and skipped (its native methods then return `UNIMPLEMENTED`) rather than failing
    /// the whole host.
    pub async fn prepare(&self) -> Result<(), Status> {
        self.native().await.map(|_| ())
    }

    /// Build (once) the [`DynamicBackend`] from the host's per-driver descriptors. Idempotent +
    /// cached via the `OnceCell`; built eagerly by [`prepare`](Self::prepare) at startup.
    async fn native(&self) -> Result<&DynamicBackend, Status> {
        self.native
            .get_or_try_init(|| async {
                let nodes = self.api.describe().await.map_err(status_from)?;
                // Each node carries a *self-contained* `FileDescriptorSet` (the interface file plus
                // its transitive well-known-type dependency files, deps-first). Collect every `file`
                // entry across all nodes into one set, deduped by file name and preserving deps-first
                // order, then build the pool ONCE so cross-file imports (e.g.
                // `google/protobuf/empty.proto`) resolve. Best-effort: an undecodable set is logged +
                // skipped rather than failing the whole host.
                let mut files: Vec<FileDescriptorProto> = Vec::new();
                let mut seen = std::collections::HashSet::new();
                for node in &nodes {
                    let Some(bytes) = &node.descriptor_set else {
                        continue;
                    };
                    let set = match FileDescriptorSet::decode(bytes.as_slice()) {
                        Ok(set) => set,
                        Err(e) => {
                            tracing::warn!(uuid = %node.uuid, error = %e, "skipping undecodable driver descriptor set");
                            continue;
                        }
                    };
                    // Multiple instances of one interface (and shared well-known-type files) repeat
                    // the same file name — register each file at most once, keeping the first
                    // occurrence's position so dependencies still precede their dependents.
                    for file in set.file {
                        if seen.insert(file.name().to_string()) {
                            files.push(file);
                        }
                    }
                }
                let pool = match DescriptorPool::from_file_descriptor_set(FileDescriptorSet { file: files }) {
                    Ok(pool) => pool,
                    Err(e) => {
                        // A bad/unresolvable set leaves the host with no native surface (legacy
                        // dispatch is unaffected) rather than failing provisioning.
                        tracing::warn!(error = %e, "native interface build failed (unresolved import?); no native surface");
                        DescriptorPool::new()
                    }
                };
                tracing::info!(native_files = pool.files().len(), "native interface ready");
                Ok(DynamicBackend::from_pool(&pool, None, self.api.clone()))
            })
            .await
    }
}

/// Map a foreign driver-call error to the `tonic::Status` remote clients observe — the
/// same code+message the Python `context.abort(...)` table produced.
fn status_from(e: DriverCallError) -> Status {
    match e {
        DriverCallError::Unimplemented(m) => Status::unimplemented(m),
        DriverCallError::InvalidArgument(m) => Status::invalid_argument(m),
        DriverCallError::DeadlineExceeded(m) => Status::deadline_exceeded(m),
        DriverCallError::NotFound(m) => Status::not_found(m),
        DriverCallError::Unknown(m) => Status::unknown(m),
    }
}

#[tonic::async_trait]
impl DriverBackend for ForeignDriver {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        let nodes = self.api.describe().await.map_err(status_from)?;
        Ok(assemble_report(&nodes))
    }

    /// Serve an opaque **native** per-driver unary call in-process: build (once) the on-demand
    /// dynamic dispatcher from this host's descriptors and route the call through it — decoding the
    /// proto request against the method descriptor, invoking the `@export` method via `driver_call`,
    /// and encoding the response. No generated servicer; the descriptor is the only schema.
    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, bytes::Bytes, MetadataMap), Status> {
        // Proto-first server seam: a host that implements its interface as a real gRPC service
        // (a generated Kotlin `PowerBackend`) decodes/dispatches/encodes the per-driver proto
        // itself, so route the opaque call straight to it — the core never touches the codec.
        // A JSON-only host (today's Python host) declines with `Unimplemented`; that — and only
        // that — falls through to the descriptor-driven `driver_call` dispatch below, so the
        // legacy path stays fully intact while proto-first hosts skip it.
        if let Some(resp) = self.try_native_unary(path, &metadata, &body).await? {
            return Ok((MetadataMap::new(), resp, MetadataMap::new()));
        }
        // Legacy/default: the descriptor-driven JSON `driver_call` dispatch (kept intact so a host
        // that does not provide the native seam — e.g. today's Python hosts — is unaffected).
        self.native().await?.forward_unary(path, metadata, body).await
    }

    /// The server-streaming half: an `@export` async generator served natively, one output message
    /// per yielded result. Routed through the same on-demand [`DynamicBackend`] as the unary path.
    ///
    /// A typed **unary** method (`On`/`Off`) is framed as streaming by the demux and arrives here
    /// too; a proto-first host serves it through its native [`ForeignNativeUnary`] seam, whose
    /// single response message is presented as a one-item stream (a unary client reads it
    /// identically). A genuinely server-streaming method (`Read`) — which `forward_unary` cannot
    /// produce — declines (`Unimplemented`) on that seam and falls through to the descriptor/JSON
    /// streaming dispatch.
    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, ResponseStream<bytes::Bytes>), Status> {
        // A typed unary method (`On`/`Off`) framed as streaming → one-item stream from the seam.
        if let Some(resp) = self.try_native_unary(path, &metadata, &body).await? {
            let stream: ResponseStream<bytes::Bytes> = Box::pin(tokio_stream::once(Ok(resp)));
            return Ok((MetadataMap::new(), stream));
        }
        // A genuinely server-streaming method (`Read`) → N-item stream from the seam.
        if let Some(stream) = self.try_native_server_stream(path, &metadata, &body).await? {
            return Ok((MetadataMap::new(), stream));
        }
        self.native().await?.forward_stream(path, metadata, body).await
    }

    /// The **native byte plane**: serve a bidi `StreamData` method (`@exportstream` console/serial,
    /// or [`ResourceService.Open`](jumpstarter_protocol::v1::ResourceService)) by bridging to the
    /// same in-process `open_stream` machinery the legacy [`open_router_stream`](Self::open_router_stream)
    /// uses — only the framing differs (`StreamData` messages + END_STREAM instead of
    /// `StreamRequest/StreamResponse` + GOAWAY). The codec pumps are shared via [`crate::stream_pump`].
    ///
    /// The demux frames *every* call as bidi, so a typed unary/server-streaming method also arrives
    /// here; it is recognised (not a byte channel) and dispatched through the typed
    /// [`forward_stream`](Self::forward_stream) on its single request frame.
    async fn forward_bidi(
        &self,
        path: &str,
        metadata: MetadataMap,
        uplink: ResponseStream<bytes::Bytes>,
    ) -> Result<(MetadataMap, ResponseStream<bytes::Bytes>), Status> {
        let native = self.native().await?;
        // Classify by path: a resource channel, an `@exportstream` channel, or a typed call. Own the
        // request JSON (`None` = typed) so no descriptor borrow is held across the pumps below.
        let request_json = if path == RESOURCE_OPEN_PATH {
            Some(resource_request_json(native, &metadata)?)
        } else {
            match native.byte_stream_export(path) {
                Some(export) => Some(exportstream_request_json(native, &metadata, export)?),
                None => None,
            }
        };
        let request_json = match request_json {
            Some(json) => json,
            None => {
                // Typed call framed as bidi: read its single request frame, dispatch typed.
                let mut uplink = uplink;
                let body = match uplink.next().await {
                    Some(Ok(b)) => b,
                    Some(Err(status)) => return Err(status),
                    None => bytes::Bytes::new(),
                };
                return self.forward_stream(path, metadata, body).await;
            }
        };

        let codec = parse_codec(&request_json);
        tracing::trace!(request = %request_json, codec = ?codec, "native byte stream open");
        let opened = self.api.open_stream(request_json).await.map_err(status_from)?;

        // Uplink pump (client -> driver): decode each StreamData → DECOMPRESS/passthrough → driver.
        let write_chan = opened.channel.clone();
        let mut uplink = uplink;
        let mut dec = codec.map(Decompressor::new);
        let mut up_fed = false;
        tokio::spawn(async move {
            loop {
                match uplink.next().await {
                    Some(Ok(frame)) => {
                        let payload = match decode_stream_data(&frame) {
                            Ok(p) => p,
                            Err(e) => {
                                tracing::debug!(error = %e, "uplink StreamData decode failed");
                                break;
                            }
                        };
                        if uplink_chunk(&mut dec, &mut up_fed, payload, &write_chan)
                            .await
                            .is_err()
                        {
                            return; // channel broken — no clean finish
                        }
                    }
                    // Clean END_STREAM: flush the decompressor tail + half-close the write side.
                    None => {
                        uplink_finish(dec.take(), up_fed, &write_chan).await;
                        return;
                    }
                    // Client uplink error: abnormal — half-close (driver reads EOF), no tail flush.
                    Some(Err(_)) => break,
                }
            }
            let _ = write_chan.close_write().await;
        });

        // Downlink pump (driver -> client): COMPRESS/passthrough → StreamData; clean EOF = END_STREAM.
        let (tx, rx) = mpsc::channel::<Result<bytes::Bytes, Status>>(PUMP_BUFFER);
        let read_chan = opened.channel.clone();
        let mut enc = codec.map(Compressor::new);
        let mut dn_fed = false;
        tokio::spawn(async move {
            loop {
                match read_chan.read().await {
                    Ok(Some(payload)) => match downlink_chunk(&mut enc, &mut dn_fed, payload) {
                        Ok(Some(out)) => {
                            if tx.send(Ok(encode_stream_data(out))).await.is_err() {
                                break;
                            }
                        }
                        Ok(None) => continue, // encoder buffered it
                        Err(msg) => {
                            let _ = tx.send(Err(Status::internal(msg))).await;
                            break;
                        }
                    },
                    Ok(None) => {
                        // Flush the compressor footer (if any), then end the stream cleanly: tonic
                        // frames END_STREAM + grpc-status OK = a clean EOF (no GOAWAY, no aclose
                        // sentinel — that legacy framing is retired on the native path).
                        if let Some(tail) = downlink_finish(enc.take(), dn_fed) {
                            let _ = tx.send(Ok(encode_stream_data(tail))).await;
                        }
                        break;
                    }
                    Err(e) => {
                        // A driver error becomes a non-OK trailer = truncation, distinguishable from
                        // the clean END_STREAM above.
                        let _ = tx.send(Err(status_from(e))).await;
                        break;
                    }
                }
            }
            let _ = read_chan.close().await;
        });

        // Accept negotiation: advertise the accepted encoding for a supported codec (Rust owns the wire).
        let mut meta = opened.initial_metadata;
        if let Some(c) = &codec {
            meta.push(("x_jmp_accept_encoding".into(), c.as_wire().into()));
        }
        Ok((to_metadata(meta), Box::pin(ReceiverStream::new(rx))))
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        let request_json = request_meta
            .to_str()
            .map_err(|_| Status::unknown("malformed `request` stream metadata"))?
            .to_string();
        // The requested wire codec (None for driver `@exportstream` → pure passthrough). Rust
        // ALWAYS supports the four codecs, so a recognized request is always accepted.
        let codec = parse_codec(&request_json);
        tracing::trace!(request = %request_json, codec = ?codec, "router stream open");
        let opened = self
            .api
            .open_stream(request_json)
            .await
            .map_err(status_from)?;

        // Uplink pump (client -> driver) = DECOMPRESS: when a codec is active, the client sent
        // compressed bytes; decompress them so the language driver receives RAW bytes (the same
        // single-side codec the Python driver did via `_resource_from_client_stream`). With no
        // codec this is verbatim relay (consoles/serial unchanged).
        let write_chan = opened.channel.clone();
        let mut uplink = uplink;
        let mut dec = codec.map(Decompressor::new);
        // Whether any DATA actually reached the decompressor. A dump has an EMPTY uplink (the driver
        // only produces the downlink), so finalizing an unfed decompressor must be skipped — some
        // codecs error on "finish with no input", and there is no tail to flush.
        let mut up_fed = false;
        tokio::spawn(async move {
            while let Some(frame) = uplink.next().await {
                match FrameType::try_from(frame.frame_type) {
                    Ok(FrameType::Data) => {
                        // DECOMPRESS (or passthrough) + write to the driver — shared with `forward_bidi`.
                        if uplink_chunk(&mut dec, &mut up_fed, frame.payload, &write_chan)
                            .await
                            .is_err()
                        {
                            break;
                        }
                    }
                    Ok(FrameType::Goaway) => {
                        // GOAWAY = clean half-close: flush the decompressor tail then close the write side.
                        uplink_finish(dec.take(), up_fed, &write_chan).await;
                        break;
                    }
                    // PING / unknown are dropped without forwarding (router.rs::classify).
                    _ => {}
                }
            }
        });

        // Downlink pump (driver -> client) = COMPRESS: when a codec is active, the driver produced
        // RAW bytes; compress them on the way out so the client decompresses. Order on EOF is
        // load-bearing: trailing compressor footer (final DATA) → GOAWAY → ABORTED "aclose"
        // (mirrors Python `_flush()` in `send_eof`/`aclose`).
        let (tx, rx) = mpsc::channel::<Result<StreamResponse, Status>>(PUMP_BUFFER);
        let read_chan = opened.channel.clone();
        let mut enc = codec.map(Compressor::new);
        // Whether the driver produced any downlink DATA. A FLASH has an empty downlink, so the
        // compressor must NOT be finalized — emitting an empty-frame footer there would surface a
        // spurious DATA frame the client writes back into the (read-only) source via its
        // bidirectional `forward_stream`. Only a DUMP (driver sends data) gets a footer.
        let mut dn_fed = false;
        let data_frame = |payload: Vec<u8>| StreamResponse {
            payload,
            frame_type: FrameType::Data as i32,
        };
        tokio::spawn(async move {
            loop {
                match read_chan.read().await {
                    Ok(Some(payload)) => match downlink_chunk(&mut enc, &mut dn_fed, payload) {
                        // COMPRESS (or passthrough) one chunk — shared with `forward_bidi`.
                        Ok(Some(out)) => {
                            if tx.send(Ok(data_frame(out))).await.is_err() {
                                break;
                            }
                        }
                        Ok(None) => continue, // encoder buffered it
                        Err(msg) => {
                            let _ = tx.send(Err(Status::internal(msg))).await;
                            break;
                        }
                    },
                    Ok(None) => {
                        // Flush the compressor footer as a FINAL DATA frame before GOAWAY (only if the
                        // driver produced data), then the legacy clean-end framing: GOAWAY + aclose.
                        if let Some(tail) = downlink_finish(enc.take(), dn_fed) {
                            let _ = tx.send(Ok(data_frame(tail))).await;
                        }
                        let goaway = StreamResponse {
                            payload: Vec::new(),
                            frame_type: FrameType::Goaway as i32,
                        };
                        let _ = tx.send(Ok(goaway)).await;
                        let _ = tx.send(Err(Status::aborted("RouterStream: aclose"))).await;
                        break;
                    }
                    Err(e) => {
                        let _ = tx.send(Err(status_from(e))).await;
                        break;
                    }
                }
            }
            let _ = read_chan.close().await;
        });

        // Rust is the source of truth for accept negotiation: ALWAYS advertise the accepted
        // encoding whenever a supported codec was requested (so the client never compresses).
        // This makes host.py's accept advertisement dead. Preserves the empty-vs-present
        // negotiation tunnel.rs relays byte-for-byte.
        let mut meta = opened.initial_metadata;
        if let Some(c) = &codec {
            meta.push(("x_jmp_accept_encoding".into(), c.as_wire().into()));
        }

        Ok(RouterStreamOpen {
            initial_metadata: to_metadata(meta),
            downlink: Box::pin(ReceiverStream::new(rx)),
        })
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        // Driver/system LogStream aggregation is deferred (the exporter already streams
        // hook logs itself); an idle stream keeps the client merge well-defined.
        Ok(Box::pin(tokio_stream::empty()))
    }
}

/// The metadata header carrying the requested wire content-encoding for a native resource open
/// (the native counterpart of the old `request` JSON's `x_jmp_content_encoding`).
const CONTENT_ENCODING_KEY: &str = "x-jmp-content-encoding";

/// Reconstruct the host `open_stream` request JSON for a native [`ResourceService.Open`] call: the
/// driver uuid (from the demux `x-jumpstarter-driver-uuid` header / fallback) plus the requested
/// wire content-encoding (`x-jmp-content-encoding` header). Wire-identical to the client's old
/// `{uuid, x_jmp_content_encoding}` resource request, so `host.open_stream` is unchanged.
#[allow(clippy::result_large_err)]
fn resource_request_json(native: &DynamicBackend, metadata: &MetadataMap) -> Result<String, Status> {
    let uuid = native.resolve_uuid(metadata).ok_or_else(|| {
        Status::invalid_argument("native resource open missing x-jumpstarter-driver-uuid header")
    })?;
    let content_encoding = metadata.get(CONTENT_ENCODING_KEY).and_then(|v| v.to_str().ok());
    Ok(serde_json::json!({
        "uuid": uuid,
        "x_jmp_content_encoding": content_encoding,
    })
    .to_string())
}

/// Reconstruct the host `open_stream` request JSON for a native `@exportstream` `Connect` call: the
/// driver uuid + the `@export` method name. Wire-identical to the client's old `{uuid, method}`.
#[allow(clippy::result_large_err)]
fn exportstream_request_json(
    native: &DynamicBackend,
    metadata: &MetadataMap,
    export: &str,
) -> Result<String, Status> {
    let uuid = native.resolve_uuid(metadata).ok_or_else(|| {
        Status::invalid_argument("native @exportstream missing x-jumpstarter-driver-uuid header")
    })?;
    Ok(serde_json::json!({ "uuid": uuid, "method": export }).to_string())
}

/// Convert the foreign host's allow-listed initial metadata into a tonic `MetadataMap`
/// (tunnel.rs further filters to the resource keys before relaying to the client).
fn to_metadata(entries: Vec<(String, String)>) -> MetadataMap {
    let mut md = MetadataMap::new();
    for (key, value) in entries {
        if let (Ok(key), Ok(value)) = (
            MetadataKey::from_bytes(key.as_bytes()),
            AsciiMetadataValue::try_from(value),
        ) {
            md.insert(key, value);
        }
    }
    md
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::DriverNode;
    use crate::host::{DriverByteChannel, DriverResultStream, DriverStreamOpen};
    use jumpstarter_protocol::v1::StreamRequest;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// A foreign host that echoes driver-call args, streams a small countdown, and serves
    /// a one-shot byte channel — enough to exercise the codec, report, and framing paths.
    struct MockHost;

    #[async_trait::async_trait]
    impl DriverApi for MockHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![DriverNode::root(
                "u1",
                HashMap::from([("jumpstarter.dev/client".to_string(), "pkg.C".to_string())]),
                Some("mock".to_string()),
                HashMap::new(),
            )])
        }

        async fn driver_call(
            &self,
            _uuid: String,
            method_name: String,
            args_json: String,
        ) -> Result<String, DriverCallError> {
            if method_name == "boom" {
                return Err(DriverCallError::Unimplemented("nope".to_string()));
            }
            // Echo: return the args array verbatim as the result.
            Ok(args_json)
        }

        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            Ok(Arc::new(Countdown {
                remaining: Mutex::new(3),
            }))
        }

        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            Ok(DriverStreamOpen {
                channel: Arc::new(OneShot {
                    sent: Mutex::new(false),
                }),
                initial_metadata: vec![("resource".to_string(), "{}".to_string())],
            })
        }
    }

    struct Countdown {
        remaining: Mutex<u32>,
    }
    #[async_trait::async_trait]
    impl DriverResultStream for Countdown {
        async fn next(&self) -> Result<Option<String>, DriverCallError> {
            let mut r = self.remaining.lock().unwrap();
            if *r == 0 {
                Ok(None)
            } else {
                *r -= 1;
                Ok(Some(format!("{}", *r)))
            }
        }
    }

    struct OneShot {
        sent: Mutex<bool>,
    }
    #[async_trait::async_trait]
    impl DriverByteChannel for OneShot {
        async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
            let mut sent = self.sent.lock().unwrap();
            if *sent {
                Ok(None)
            } else {
                *sent = true;
                Ok(Some(b"hello".to_vec()))
            }
        }
        async fn write(&self, _data: Vec<u8>) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close_write(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
    }

    fn host() -> ForeignDriver {
        ForeignDriver::new(Arc::new(MockHost))
    }

    #[tokio::test]
    async fn get_report_assembles_from_describe() {
        let report = host().get_report().await.unwrap();
        assert_eq!(report.reports.len(), 1);
        assert_eq!(report.reports[0].uuid, "u1");
        assert_eq!(report.reports[0].description.as_deref(), Some("mock"));
    }

    #[tokio::test]
    async fn router_stream_frames_bytes_and_synthesizes_aclose() {
        // Empty uplink (client sends nothing, then half-closes).
        let (_tx, rx) = mpsc::channel::<StreamRequest>(1);
        let meta = AsciiMetadataValue::try_from("{\"uuid\":\"u1\"}").unwrap();
        let opened = host()
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Initial metadata carries the resource key.
        assert!(opened.initial_metadata.get("resource").is_some());

        // Downlink: one DATA("hello"), then GOAWAY, then the trailing aclose status.
        let mut downlink = opened.downlink;
        let first = downlink.next().await.unwrap().unwrap();
        assert_eq!(first.frame_type, FrameType::Data as i32);
        assert_eq!(first.payload, b"hello");

        let goaway = downlink.next().await.unwrap().unwrap();
        assert_eq!(goaway.frame_type, FrameType::Goaway as i32);

        let trailing = downlink.next().await.unwrap().unwrap_err();
        assert_eq!(trailing.code(), tonic::Code::Aborted);
        assert_eq!(trailing.message(), "RouterStream: aclose");

        assert!(downlink.next().await.is_none());
    }

    // --- codec seam (resource flash/dump): uplink DECOMPRESS, downlink COMPRESS ----------

    /// A host whose `open_stream` reflects the request so the codec is parsed, and whose channel
    /// records the RAW bytes the driver received (uplink) and serves a fixed RAW payload (downlink).
    struct CodecHost {
        recorded: Arc<Mutex<Vec<u8>>>,
        downlink_payload: Vec<u8>,
    }

    #[async_trait::async_trait]
    impl DriverApi for CodecHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![])
        }
        async fn driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<String, DriverCallError> {
            unreachable!()
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            unreachable!()
        }
        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            Ok(DriverStreamOpen {
                channel: Arc::new(RecordingChannel {
                    recorded: self.recorded.clone(),
                    downlink: Mutex::new(Some(self.downlink_payload.clone())),
                }),
                initial_metadata: vec![("resource".to_string(), "{}".to_string())],
            })
        }
    }

    struct RecordingChannel {
        recorded: Arc<Mutex<Vec<u8>>>,
        downlink: Mutex<Option<Vec<u8>>>,
    }
    #[async_trait::async_trait]
    impl DriverByteChannel for RecordingChannel {
        async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
            Ok(self.downlink.lock().unwrap().take())
        }
        async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
            self.recorded.lock().unwrap().extend_from_slice(&data);
            Ok(())
        }
        async fn close_write(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
    }

    /// gzip-compress a blob in one shot via the codec (what a client would send on the wire).
    fn gzip(raw: &[u8]) -> Vec<u8> {
        let mut c = Compressor::new(Codec::Gzip);
        let mut out = c.compress(raw).unwrap();
        out.extend(c.finish().unwrap());
        out
    }

    /// gzip-decompress a blob via the codec (what a client would do on read).
    fn gunzip(blob: &[u8]) -> Vec<u8> {
        let mut d = Decompressor::new(Codec::Gzip);
        let mut out = d.decompress(blob).unwrap();
        out.extend(d.finish().unwrap());
        out
    }

    #[tokio::test]
    async fn codec_uplink_decompresses_and_downlink_compresses_and_advertises_accept() {
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let raw_image = b"the quick brown fox".repeat(500);
        let raw_dump = b"DUMP payload from the device".repeat(300);
        let host = ForeignDriver::new(Arc::new(CodecHost {
            recorded: recorded.clone(),
            downlink_payload: raw_dump.clone(),
        }));

        // Client sends gzip-compressed image bytes (split across two frames), then half-closes.
        let blob = gzip(&raw_image);
        let (mid, rest) = blob.split_at(blob.len() / 2);
        let (tx, rx) = mpsc::channel::<StreamRequest>(8);
        tx.send(StreamRequest {
            payload: mid.to_vec(),
            frame_type: FrameType::Data as i32,
        })
        .await
        .unwrap();
        tx.send(StreamRequest {
            payload: rest.to_vec(),
            frame_type: FrameType::Data as i32,
        })
        .await
        .unwrap();
        tx.send(StreamRequest {
            payload: Vec::new(),
            frame_type: FrameType::Goaway as i32,
        })
        .await
        .unwrap();
        drop(tx);

        let meta =
            AsciiMetadataValue::try_from("{\"uuid\":\"u1\",\"x_jmp_content_encoding\":\"gzip\"}")
                .unwrap();
        let opened = host
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Rust ALWAYS advertises accept for a supported codec.
        assert_eq!(
            opened
                .initial_metadata
                .get("x_jmp_accept_encoding")
                .and_then(|v| v.to_str().ok()),
            Some("gzip")
        );

        // Drain the downlink: concatenate the compressed DATA frames, stop at GOAWAY.
        let mut downlink = opened.downlink;
        let mut compressed_down = Vec::new();
        loop {
            match downlink.next().await {
                Some(Ok(frame)) if frame.frame_type == FrameType::Data as i32 => {
                    compressed_down.extend(frame.payload)
                }
                Some(Ok(_)) => break, // GOAWAY
                Some(Err(_)) => break, // trailing aborted
                None => break,
            }
        }

        // The driver received the RAW (decompressed) image.
        assert_eq!(*recorded.lock().unwrap(), raw_image);
        // The client would receive the RAW dump back after decompressing the downlink.
        assert_eq!(gunzip(&compressed_down), raw_dump);
    }

    /// Load a golden fixture produced by the venv Python compressors (shared with the
    /// jumpstarter-compression crate). Path is relative to this crate's manifest dir.
    fn python_golden_fixture(name: &str) -> Vec<u8> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../jumpstarter-compression/tests/fixtures")
            .join(name);
        std::fs::read(&path).unwrap_or_else(|e| panic!("read golden fixture {}: {e}", path.display()))
    }

    /// END-TO-END host decompression guard (silent-corruption): feed a *Python-zstd*
    /// golden frame through the real host router seam (the same `open_router_stream`
    /// path the live exporter uses) in multiple wire chunks, and assert the Python
    /// MockStorage-equivalent driver records EXACTLY the original uncompressed bytes.
    ///
    /// This is the airtight, no-cancellation-of-symmetric-bugs direction: the
    /// compressed bytes come from Python (backports.zstd, level 3), NOT from Rust's
    /// own compressor, so a wrong Rust decoder cannot accidentally agree with a
    /// wrong Rust encoder. Original/compressed fixtures are the same ones the
    /// jumpstarter-compression golden tests pin by sha256 in MANIFEST.json.
    #[tokio::test]
    async fn host_seam_decompresses_python_zstd_golden_to_original_bytes() {
        let original = python_golden_fixture("input_random.bin");
        let python_zstd = python_golden_fixture("random.zstd");
        // Guard the fixtures themselves so a bad checkout can't make this pass vacuously.
        assert_eq!(original.len(), 262144, "golden original len drifted");
        assert_ne!(python_zstd, original, "compressed fixture must differ from raw");

        let recorded = Arc::new(Mutex::new(Vec::new()));
        let host = ForeignDriver::new(Arc::new(CodecHost {
            recorded: recorded.clone(),
            downlink_payload: Vec::new(),
        }));

        // Send the Python zstd frame split across THREE odd-sized wire DATA frames to
        // exercise the streaming decoder's buffering across chunk boundaries, then GOAWAY.
        let (tx, rx) = mpsc::channel::<StreamRequest>(8);
        let n = python_zstd.len();
        let bounds = [0, n / 3, (2 * n) / 3, n];
        for w in bounds.windows(2) {
            tx.send(StreamRequest {
                payload: python_zstd[w[0]..w[1]].to_vec(),
                frame_type: FrameType::Data as i32,
            })
            .await
            .unwrap();
        }
        tx.send(StreamRequest {
            payload: Vec::new(),
            frame_type: FrameType::Goaway as i32,
        })
        .await
        .unwrap();
        drop(tx);

        let meta =
            AsciiMetadataValue::try_from("{\"uuid\":\"u1\",\"x_jmp_content_encoding\":\"zstd\"}")
                .unwrap();
        let opened = host
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Host always advertises zstd accept-encoding for this supported codec.
        assert_eq!(
            opened
                .initial_metadata
                .get("x_jmp_accept_encoding")
                .and_then(|v| v.to_str().ok()),
            Some("zstd")
        );

        // Drain the (empty) downlink to let the uplink pump run to completion.
        let mut downlink = opened.downlink;
        while let Some(item) = downlink.next().await {
            if item.is_err() {
                break;
            }
        }

        // The driver received EXACTLY the original uncompressed bytes — i.e. the host
        // decompression seam delivered DECOMPRESSED data, byte-identical to the source.
        assert_eq!(
            *recorded.lock().unwrap(),
            original,
            "host seam did not deliver the original bytes from Python zstd"
        );
    }

    // --- native descriptor-set flow (the missing link): a self-contained FileDescriptorSet that
    // imports google/protobuf/empty.proto must build a pool and route native calls --------------

    /// A foreign host that advertises a PowerInterface descriptor whose On/Off/Read methods use
    /// `google.protobuf.Empty` — i.e. the descriptor *imports* `google/protobuf/empty.proto`, the
    /// case the old per-FDP path could not resolve. Records the driver calls it receives so the
    /// test can assert the native `On` reached the driver.
    struct PowerDescHost {
        descriptor_set: Vec<u8>,
        calls: Arc<Mutex<Vec<(String, String, String)>>>,
    }

    #[async_trait::async_trait]
    impl DriverApi for PowerDescHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![DriverNode {
                uuid: "power-1".into(),
                parent_uuid: None,
                labels: HashMap::from([(
                    "jumpstarter.dev/client".to_string(),
                    "pkg.PowerClient".to_string(),
                )]),
                description: Some("mock power".into()),
                methods_description: HashMap::new(),
                descriptor_set: Some(self.descriptor_set.clone()),
            }])
        }
        async fn driver_call(
            &self,
            uuid: String,
            method_name: String,
            args_json: String,
        ) -> Result<String, DriverCallError> {
            self.calls.lock().unwrap().push((uuid, method_name, args_json));
            Ok("null".into()) // On() returns Empty
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            unreachable!()
        }
        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            unreachable!()
        }
    }

    // --- native byte plane (forward_bidi): @exportstream + resource over StreamData ---------

    /// A descriptor set for a console-style interface with a bidi `StreamData` `@exportstream`
    /// method `Connect` — the shape `byte_stream_export` keys on. Built by hand (no protoc).
    fn console_descriptor_set_bytes() -> Vec<u8> {
        use prost_reflect::prost_types::field_descriptor_proto::{Label, Type};
        use prost_reflect::prost_types::{
            DescriptorProto, FieldDescriptorProto, MethodDescriptorProto, ServiceDescriptorProto,
        };
        let stream_data = DescriptorProto {
            name: Some("StreamData".into()),
            field: vec![FieldDescriptorProto {
                name: Some("payload".into()),
                number: Some(1),
                label: Some(Label::Optional as i32),
                r#type: Some(Type::Bytes as i32),
                ..Default::default()
            }],
            ..Default::default()
        };
        let console_file = FileDescriptorProto {
            name: Some("console.proto".into()),
            package: Some("jumpstarter.interfaces.console.v1".into()),
            message_type: vec![stream_data],
            service: vec![ServiceDescriptorProto {
                name: Some("ConsoleInterface".into()),
                method: vec![MethodDescriptorProto {
                    name: Some("Connect".into()),
                    input_type: Some(".jumpstarter.interfaces.console.v1.StreamData".into()),
                    output_type: Some(".jumpstarter.interfaces.console.v1.StreamData".into()),
                    client_streaming: Some(true),
                    server_streaming: Some(true),
                    ..Default::default()
                }],
                ..Default::default()
            }],
            syntax: Some("proto3".into()),
            ..Default::default()
        };
        FileDescriptorSet {
            file: vec![console_file],
        }
        .encode_to_vec()
    }

    /// A byte channel that echoes: each `write` (uplink) is queued and returned by a later `read`
    /// (downlink). `close_write` closes the queue so `read` reaches EOF after draining — modelling a
    /// console/resource that loops the client's bytes back.
    struct EchoChannel {
        tx: Mutex<Option<mpsc::UnboundedSender<Vec<u8>>>>,
        rx: tokio::sync::Mutex<mpsc::UnboundedReceiver<Vec<u8>>>,
    }
    impl EchoChannel {
        fn new() -> Arc<Self> {
            let (tx, rx) = mpsc::unbounded_channel();
            Arc::new(Self {
                tx: Mutex::new(Some(tx)),
                rx: tokio::sync::Mutex::new(rx),
            })
        }
    }
    #[async_trait::async_trait]
    impl DriverByteChannel for EchoChannel {
        async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
            Ok(self.rx.lock().await.recv().await)
        }
        async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
            if let Some(tx) = self.tx.lock().unwrap().as_ref() {
                let _ = tx.send(data);
            }
            Ok(())
        }
        async fn close_write(&self) -> Result<(), DriverCallError> {
            *self.tx.lock().unwrap() = None; // close the queue → read reaches EOF after drain
            Ok(())
        }
        async fn close(&self) -> Result<(), DriverCallError> {
            *self.tx.lock().unwrap() = None;
            Ok(())
        }
    }

    /// A host that records the `open_stream` request JSON and serves an [`EchoChannel`]; advertises
    /// the console descriptor so the `Connect` method is recognised as a byte channel.
    struct EchoHost {
        request: Arc<Mutex<Option<String>>>,
    }
    #[async_trait::async_trait]
    impl DriverApi for EchoHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![DriverNode {
                uuid: "echo-1".into(),
                parent_uuid: None,
                labels: HashMap::from([(
                    "jumpstarter.dev/client".to_string(),
                    "pkg.ConsoleClient".to_string(),
                )]),
                description: Some("echo console".into()),
                methods_description: HashMap::new(),
                descriptor_set: Some(console_descriptor_set_bytes()),
            }])
        }
        async fn driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<String, DriverCallError> {
            unreachable!()
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            unreachable!()
        }
        async fn open_stream(
            &self,
            request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            *self.request.lock().unwrap() = Some(request_json);
            Ok(DriverStreamOpen {
                channel: EchoChannel::new(),
                initial_metadata: vec![("resource".to_string(), "{}".to_string())],
            })
        }
    }

    /// Build a uplink of `StreamData`-encoded chunks followed by a clean END_STREAM (the iterator
    /// simply ending), as `forward_bidi` consumes it.
    fn stream_data_uplink(chunks: &[&[u8]]) -> ResponseStream<bytes::Bytes> {
        let frames: Vec<Result<bytes::Bytes, Status>> = chunks
            .iter()
            .map(|c| Ok(encode_stream_data(c.to_vec())))
            .collect();
        Box::pin(tokio_stream::iter(frames))
    }

    fn uuid_md(uuid: &str) -> MetadataMap {
        let mut md = MetadataMap::new();
        md.insert(crate::dynamic_backend::DRIVER_UUID_KEY, uuid.parse().unwrap());
        md
    }

    #[tokio::test]
    async fn forward_bidi_exportstream_echoes_stream_data_and_reconstructs_request() {
        let request = Arc::new(Mutex::new(None));
        let host = ForeignDriver::new(Arc::new(EchoHost {
            request: request.clone(),
        }));
        host.prepare().await.unwrap();

        let chunks: &[&[u8]] = &[b"one", b"two", b"three"];
        let (_meta, mut downlink) = host
            .forward_bidi(
                "/jumpstarter.interfaces.console.v1.ConsoleInterface/Connect",
                uuid_md("echo-1"),
                stream_data_uplink(chunks),
            )
            .await
            .unwrap();

        // The echoed chunks come back as StreamData, in order, then a CLEAN end (no error item).
        let mut got: Vec<Vec<u8>> = Vec::new();
        while let Some(item) = downlink.next().await {
            let frame = item.expect("clean EOF must not surface an error");
            got.push(decode_stream_data(&frame).unwrap());
        }
        assert_eq!(got, vec![b"one".to_vec(), b"two".to_vec(), b"three".to_vec()]);

        // The native path reconstructed the host request JSON: {uuid, method:"connect"} (no codec).
        let req: serde_json::Value =
            serde_json::from_str(request.lock().unwrap().as_ref().unwrap()).unwrap();
        assert_eq!(req["uuid"], "echo-1");
        assert_eq!(req["method"], "connect");
    }

    #[tokio::test]
    async fn forward_bidi_resource_open_reconstructs_resource_request() {
        let request = Arc::new(Mutex::new(None));
        let host = ForeignDriver::new(Arc::new(EchoHost {
            request: request.clone(),
        }));
        host.prepare().await.unwrap();

        let chunks: &[&[u8]] = &[b"flash-bytes"];
        let (meta, mut downlink) = host
            .forward_bidi(RESOURCE_OPEN_PATH, uuid_md("echo-1"), stream_data_uplink(chunks))
            .await
            .unwrap();

        // The resource handle rides the response initial metadata, unchanged from the host.
        assert!(meta.get("resource").is_some());

        let mut got: Vec<Vec<u8>> = Vec::new();
        while let Some(item) = downlink.next().await {
            got.push(decode_stream_data(&item.unwrap()).unwrap());
        }
        assert_eq!(got, vec![b"flash-bytes".to_vec()]);

        // Resource request JSON: {uuid, x_jmp_content_encoding:null} — and crucially NO `method`.
        let req: serde_json::Value =
            serde_json::from_str(request.lock().unwrap().as_ref().unwrap()).unwrap();
        assert_eq!(req["uuid"], "echo-1");
        assert!(req.get("method").is_none(), "a resource open carries no method");
        assert!(req.get("x_jmp_content_encoding").is_some());
    }

    #[tokio::test]
    async fn forward_bidi_typed_call_falls_back_to_typed_dispatch() {
        // A typed unary method (PowerInterface/On) framed as bidi must NOT be treated as a byte
        // channel — forward_bidi reads its single request frame and dispatches typed.
        let calls = Arc::new(Mutex::new(Vec::new()));
        let host = ForeignDriver::new(Arc::new(PowerDescHost {
            descriptor_set: power_descriptor_set_bytes(),
            calls: calls.clone(),
        }));
        host.prepare().await.unwrap();

        // A typed On request is a single raw (Empty) message, NOT StreamData-wrapped.
        let uplink: ResponseStream<bytes::Bytes> =
            Box::pin(tokio_stream::once(Ok(bytes::Bytes::new())));
        let (_meta, mut downlink) = host
            .forward_bidi(
                "/jumpstarter.interfaces.power.v1.PowerInterface/On",
                uuid_md("power-1"),
                uplink,
            )
            .await
            .unwrap();

        // The typed dispatch yields one (empty) response message, then clean end.
        let first = downlink.next().await.unwrap().unwrap();
        assert!(first.is_empty(), "On() returns Empty → empty response message");

        let (uuid, method, args) = calls.lock().unwrap().last().cloned().unwrap();
        assert_eq!((uuid.as_str(), method.as_str(), args.as_str()), ("power-1", "on", "[]"));
    }

    /// Build the exact self-contained `FileDescriptorSet` the Python host produces for
    /// `PowerInterface`: the interface file (package `jumpstarter.interfaces.power.v1`, service
    /// `PowerInterface` with `On`/`Off` Empty→Empty unary) **plus** the `google/protobuf/empty.proto`
    /// dependency file it imports, ordered deps-first. Serialized to bytes (what crosses the FFI).
    fn power_descriptor_set_bytes() -> Vec<u8> {
        use prost_reflect::prost_types::field_descriptor_proto::{Label, Type};
        use prost_reflect::prost_types::{
            DescriptorProto, FieldDescriptorProto, MethodDescriptorProto, ServiceDescriptorProto,
        };

        // The well-known empty.proto file (package google.protobuf, message Empty, no fields).
        let empty_file = FileDescriptorProto {
            name: Some("google/protobuf/empty.proto".into()),
            package: Some("google.protobuf".into()),
            message_type: vec![DescriptorProto {
                name: Some("Empty".into()),
                ..Default::default()
            }],
            syntax: Some("proto3".into()),
            ..Default::default()
        };

        // The interface file, importing empty.proto (the case the bare-FDP path couldn't resolve).
        let unary = |name: &str| MethodDescriptorProto {
            name: Some(name.into()),
            input_type: Some(".google.protobuf.Empty".into()),
            output_type: Some(".google.protobuf.Empty".into()),
            ..Default::default()
        };
        // A SetVoltage method with a real request message so we also exercise a non-Empty path.
        let set_voltage_req = DescriptorProto {
            name: Some("SetVoltageRequest".into()),
            field: vec![FieldDescriptorProto {
                name: Some("millivolts".into()),
                number: Some(1),
                label: Some(Label::Optional as i32),
                r#type: Some(Type::Int64 as i32),
                ..Default::default()
            }],
            ..Default::default()
        };
        let power_file = FileDescriptorProto {
            name: Some("power.proto".into()),
            package: Some("jumpstarter.interfaces.power.v1".into()),
            dependency: vec!["google/protobuf/empty.proto".into()],
            message_type: vec![set_voltage_req],
            service: vec![ServiceDescriptorProto {
                name: Some("PowerInterface".into()),
                method: vec![
                    unary("On"),
                    unary("Off"),
                    MethodDescriptorProto {
                        name: Some("SetVoltage".into()),
                        input_type: Some(
                            ".jumpstarter.interfaces.power.v1.SetVoltageRequest".into(),
                        ),
                        output_type: Some(".google.protobuf.Empty".into()),
                        ..Default::default()
                    },
                ],
                ..Default::default()
            }],
            syntax: Some("proto3".into()),
            ..Default::default()
        };

        // deps-first: empty.proto precedes the file that imports it.
        FileDescriptorSet {
            file: vec![empty_file, power_file],
        }
        .encode_to_vec()
    }

    /// END-TO-END the new link: a self-contained descriptor SET (importing empty.proto) flows from
    /// `describe()` → `ForeignDriver::native()` (decode set, merge deps-first, build ONE pool) →
    /// `DynamicBackend` → a native `/…/PowerInterface/On` unary call reaches the driver. The old
    /// per-FDP path skipped this descriptor (unresolved `google/protobuf/empty.proto` import).
    #[tokio::test]
    async fn native_descriptor_set_resolves_empty_import_and_routes_on() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let host = ForeignDriver::new(Arc::new(PowerDescHost {
            descriptor_set: power_descriptor_set_bytes(),
            calls: calls.clone(),
        }));

        // Eager prepare must succeed (the import resolves now) and leave the native surface ready.
        host.prepare().await.expect("native interface built from descriptor set");

        // A native unary On call (Empty request → empty body) routed via the uuid header.
        let mut md = MetadataMap::new();
        md.insert(
            crate::dynamic_backend::DRIVER_UUID_KEY,
            "power-1".parse().unwrap(),
        );
        let (_resp_md, resp_body, _trailers) = host
            .forward_unary(
                "/jumpstarter.interfaces.power.v1.PowerInterface/On",
                md,
                bytes::Bytes::new(),
            )
            .await
            .expect("native On call dispatches");

        // On() returns Empty → empty response body.
        assert!(resp_body.is_empty(), "Empty response must be empty bytes");

        // The mock driver was driven: uuid from header, @export name `on`, no args.
        let (uuid, method, args) = calls.lock().unwrap().last().cloned().unwrap();
        assert_eq!(uuid, "power-1");
        assert_eq!(method, "on");
        assert_eq!(args, "[]");
    }
}
