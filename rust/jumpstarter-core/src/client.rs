//! Client-side driver-call surface.
//!
//! Connects to a `JUMPSTARTER_HOST` transport socket and invokes drivers, mirroring the
//! Python `AsyncDriverClient`'s gRPC calls so the Python driver clients (and `j`) can
//! route through the Rust core (FFI) instead of grpcio + generated stubs. Rust owns the
//! value codec and the wire protocol; args/results cross as plain JSON. This is the
//! consumer mirror of [`crate::foreign::ForeignDriver`].

use std::pin::Pin;
use std::sync::Arc;

use jumpstarter_client::exporter_logs::uds_channel;
use jumpstarter_protocol::router::{classify, data_frame, goaway_frame, FrameAction};
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{
    EndSessionRequest, GetStatusRequest, LogStreamResponse, StreamRequest, StreamResponse,
};
use tokio::sync::{mpsc, Mutex, OnceCell};
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::{Stream, StreamExt as _};
use tonic::metadata::AsciiMetadataValue;
use tonic::service::interceptor::InterceptedService;
use tonic::transport::{Channel, Endpoint};
use tonic::{Code, Request, Status, Streaming};

use crate::dynamic::{decode_response, encode_request};
use crate::error::DriverCallError;

/// Resource initial-metadata keys the host emits and the client consumes
/// (`driver/base.py:189-198`); the same allow-list `tunnel.rs` relays.
const RELAY_KEYS: [&str; 2] = ["resource", "x_jmp_accept_encoding"];

/// Map a wire `tonic::Status` to the driver-call error taxonomy the Python client maps to
/// its exceptions (`NOT_FOUND`→DriverMethodNotImplemented, `INVALID_ARGUMENT`→
/// DriverInvalidArgument, …) — the inverse of the host-side mapping.
fn err_from_status(status: Status) -> DriverCallError {
    let msg = status.message().to_string();
    match status.code() {
        Code::Unimplemented => DriverCallError::Unimplemented(msg),
        Code::InvalidArgument => DriverCallError::InvalidArgument(msg),
        Code::DeadlineExceeded => DriverCallError::DeadlineExceeded(msg),
        Code::NotFound => DriverCallError::NotFound(msg),
        _ => DriverCallError::Unknown(msg),
    }
}

/// Attaches the `x-jumpstarter-passphrase` metadata to each request when connected to a
/// standalone exporter (`jmp shell --tls-grpc --passphrase`); a no-op for the UDS path.
#[derive(Clone)]
struct PassphraseInterceptor {
    passphrase: Option<AsciiMetadataValue>,
}

impl tonic::service::Interceptor for PassphraseInterceptor {
    fn call(&mut self, mut request: Request<()>) -> Result<Request<()>, Status> {
        if let Some(passphrase) = &self.passphrase {
            request
                .metadata_mut()
                .insert("x-jumpstarter-passphrase", passphrase.clone());
        }
        Ok(request)
    }
}

use crate::native_table::{build_native_table, NativeTable};

/// A connection to an exporter — either via its local `JUMPSTARTER_HOST` transport socket
/// (lease/local mode), or directly to a standalone exporter's TCP gRPC (`jmp shell --tls-grpc`).
pub struct ClientSession {
    channel: Channel,
    passphrase: Option<AsciiMetadataValue>,
    /// Lazily-built (on first `driver_call`) native dispatch table. Cached for the session — the
    /// driver tree + its descriptors are fixed for a lease's lifetime.
    native: OnceCell<NativeTable>,
}

impl ClientSession {
    /// Connect to the `JUMPSTARTER_HOST` the shell set: a UDS transport-socket path, or a bare
    /// `host:port` for a standalone exporter (direct mode). Direct mode reads
    /// `JMP_GRPC_INSECURE`/`JMP_GRPC_PASSPHRASE` from the env (`jmp shell --tls-grpc` sets them).
    pub async fn connect(host: String) -> Result<Self, DriverCallError> {
        // A UDS transport socket is a filesystem path; a direct target is a bare `host:port`.
        if host.contains('/') {
            let channel = uds_channel(host).await.map_err(DriverCallError::Unknown)?;
            return Ok(Self {
                channel,
                passphrase: None,
                native: OnceCell::new(),
            });
        }
        // Direct mode: connect to the standalone exporter's plaintext-h2c gRPC (the only
        // standalone exporter mode today). The passphrase is attached per-RPC by the interceptor.
        let insecure = std::env::var("JMP_GRPC_INSECURE").is_ok_and(|v| v == "1" || v == "true");
        if !insecure {
            return Err(DriverCallError::Unknown(
                "direct exporter connection over TLS is not yet supported (use --tls-grpc-insecure)"
                    .to_string(),
            ));
        }
        let endpoint = Endpoint::from_shared(format!("http://{host}"))
            .map_err(|e| DriverCallError::Unknown(e.to_string()))?
            // Large HTTP/2 windows so bulk resource/flash transfers aren't window-gated.
            .initial_stream_window_size(8 * 1024 * 1024)
            .initial_connection_window_size(16 * 1024 * 1024);
        // Connect on the multi-threaded IO runtime so the connection driver doesn't run on
        // async-compat's single thread (see `jumpstarter_client::io_runtime`).
        let channel = jumpstarter_client::io_runtime()
            .spawn(async move { endpoint.connect().await })
            .await
            .map_err(|e| DriverCallError::Unknown(format!("connect task panicked: {e}")))?
            .map_err(|e| DriverCallError::Unknown(format!("connecting to direct exporter {host}: {e}")))?;
        let passphrase = std::env::var(jumpstarter_config::env::JMP_GRPC_PASSPHRASE)
            .ok()
            .filter(|p| !p.is_empty())
            .and_then(|p| AsciiMetadataValue::try_from(p).ok());
        // The client always speaks plain gRPC; the shared-memory byte plane is contained entirely in
        // the hub↔driver-host hop (the hub bridges the ring to/from this gRPC stream).
        Ok(Self {
            channel,
            passphrase,
            native: OnceCell::new(),
        })
    }

    fn auth(&self) -> PassphraseInterceptor {
        PassphraseInterceptor {
            passphrase: self.passphrase.clone(),
        }
    }

    fn exporter(&self) -> ExporterServiceClient<InterceptedService<Channel, PassphraseInterceptor>> {
        ExporterServiceClient::with_interceptor(self.channel.clone(), self.auth())
    }

    /// `GetReport` → a JSON array of the driver tree (uuid/parent/labels/methods), which
    /// the Python client uses to build its client object graph.
    pub async fn get_report(&self) -> Result<String, DriverCallError> {
        tracing::debug!(method = "GetReport", "rpc");
        let report = self
            .exporter()
            .get_report(())
            .await
            .map_err(err_from_status)?
            .into_inner();
        let nodes: Vec<serde_json::Value> = report
            .reports
            .iter()
            .map(|r| {
                serde_json::json!({
                    "uuid": r.uuid,
                    "parent_uuid": r.parent_uuid,
                    "labels": r.labels,
                    "description": r.description,
                    "methods_description": r.methods_description,
                })
            })
            .collect();
        serde_json::to_string(&nodes).map_err(|e| DriverCallError::Unknown(e.to_string()))
    }

    /// Build (once) the native dispatch table from the descriptors `GetReport` ships. Each driver
    /// instance's `descriptor_set` is a self-contained `FileDescriptorSet`; we merge them all into
    /// one `DescriptorPool` (deduping shared well-known-type files), then index every
    /// `(uuid, @export-name)` to its method's path + input/output descriptors. A driver whose node
    /// has no descriptor set contributes nothing (legacy dispatch only). Cached for the session.
    async fn native_table(&self) -> Result<&NativeTable, DriverCallError> {
        self.native
            .get_or_try_init(|| async {
                let report = self
                    .exporter()
                    .get_report(())
                    .await
                    .map_err(err_from_status)?
                    .into_inner();
                Ok(build_native_table(&report.reports))
            })
            .await
    }

    /// Invoke a unary driver call: `args_json` is a JSON array, returns the JSON result.
    ///
    /// **Native-primary** (decision #10 — the Python API is unchanged; the bridging happens here):
    /// when the driver's interface descriptor is known, the args are encoded into the native request
    /// message and dispatched through [`native_unary`](Self::native_unary); the response message is
    /// decoded back to the JSON result. Native is the **only** path: every driver ships a
    /// descriptor (the host introspects each driver's `@export` surface), so a missing route is a
    /// hard error, not a fallback.
    pub async fn driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<String, DriverCallError> {
        tracing::debug!(method = %method, uuid = %uuid, "rpc");

        let table = self.native_table().await?;
        let route = table.get(&(uuid.clone(), method.clone())).ok_or_else(|| {
            DriverCallError::Unimplemented(format!(
                "no native route for {uuid}/{method}: the driver ships no descriptor for this method"
            ))
        })?;
        tracing::debug!(method = %method, uuid = %uuid, path = %route.path, "native driver_call");
        let body = encode_request(&route.input, &args_json)?;
        let resp = self
            .native_unary(uuid.clone(), route.path.clone(), body)
            .await?;
        decode_response(&route.output, &resp)
    }

    /// Invoke an opaque **native** per-driver unary gRPC call — the client side of the native calls
    /// surface. `path` is the full gRPC method path (`/jumpstarter.driver.power.v1.PowerInterface/On`),
    /// `body` the encoded request message bytes; `uuid` is attached as the `x-jumpstarter-driver-uuid`
    /// header so the exporter demux routes to the right driver instance. Returns the response message
    /// bytes. The core never deserializes the per-driver proto — the language stub owns
    /// (de)serialization; this just carries the opaque message + the auth/uuid metadata.
    pub async fn native_unary(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Vec<u8>, DriverCallError> {
        use std::str::FromStr;
        tracing::debug!(path = %path, uuid = %uuid, "native rpc");
        let path = tonic::codegen::http::uri::PathAndQuery::from_str(&path)
            .map_err(|e| DriverCallError::Unknown(format!("invalid method path {path:?}: {e}")))?;
        let mut grpc =
            tonic::client::Grpc::new(InterceptedService::new(self.channel.clone(), self.auth()));
        grpc.ready()
            .await
            .map_err(|e| DriverCallError::Unknown(format!("native channel not ready: {e}")))?;
        let mut req = Request::new(bytes::Bytes::from(body));
        let uuid_val = AsciiMetadataValue::from_str(&uuid)
            .map_err(|e| DriverCallError::Unknown(format!("invalid driver uuid {uuid:?}: {e}")))?;
        req.metadata_mut()
            .insert("x-jumpstarter-driver-uuid", uuid_val);
        let resp = grpc
            .unary(req, path, jumpstarter_transport::demux::BytesCodec)
            .await
            .map_err(err_from_status)?;
        Ok(resp.into_inner().to_vec())
    }

    /// Invoke a streaming driver call; results are pulled JSON-at-a-time from the returned
    /// [`ClientResultStream`].
    ///
    /// Native is the **only** path (the streaming mirror of [`driver_call`](Self::driver_call)):
    /// the `(uuid, method)` route encodes the request, opens the call over
    /// [`native_server_stream`](Self::native_server_stream), and decodes each response message back
    /// to the JSON result. A missing route is a hard error, not a fallback.
    pub async fn streaming_driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<Arc<ClientResultStream>, DriverCallError> {
        tracing::debug!(method = %method, uuid = %uuid, "rpc");
        let label = format!("{uuid}/{method}");

        let table = self.native_table().await?;
        let route = table.get(&(uuid.clone(), method.clone())).ok_or_else(|| {
            DriverCallError::Unimplemented(format!(
                "no native route for {uuid}/{method}: the driver ships no descriptor for this method"
            ))
        })?;
        tracing::debug!(method = %method, uuid = %uuid, path = %route.path, "native streaming_driver_call");
        let body = encode_request(&route.input, &args_json)?;
        let output = route.output.clone();
        let native = self
            .native_server_stream(uuid.clone(), route.path.clone(), body)
            .await?;
        let mapped = native.map(move |item| match item {
            Ok(bytes) => decode_response(&output, &bytes),
            Err(status) => Err(err_from_status(status)),
        });
        Ok(Arc::new(ClientResultStream {
            inner: Mutex::new(Box::pin(mapped)),
            label,
        }))
    }

    /// Invoke an opaque **native** per-driver **server-streaming** gRPC call — the client side of
    /// the native streaming surface, the streaming mirror of [`native_unary`](Self::native_unary).
    /// `path` is the full gRPC method path, `body` the encoded request message; `uuid` rides the
    /// `x-jumpstarter-driver-uuid` header so the exporter demux routes to the right instance.
    /// Returns the raw response-message stream (opaque proto bytes the caller decodes).
    pub async fn native_server_stream(
        &self,
        uuid: String,
        path: String,
        body: Vec<u8>,
    ) -> Result<Streaming<bytes::Bytes>, DriverCallError> {
        use std::str::FromStr;
        tracing::debug!(path = %path, uuid = %uuid, "native streaming rpc");
        let path = tonic::codegen::http::uri::PathAndQuery::from_str(&path)
            .map_err(|e| DriverCallError::Unknown(format!("invalid method path {path:?}: {e}")))?;
        let mut grpc =
            tonic::client::Grpc::new(InterceptedService::new(self.channel.clone(), self.auth()))
                .max_decoding_message_size(64 * 1024 * 1024)
                .max_encoding_message_size(64 * 1024 * 1024);
        grpc.ready()
            .await
            .map_err(|e| DriverCallError::Unknown(format!("native channel not ready: {e}")))?;
        let mut req = Request::new(bytes::Bytes::from(body));
        let uuid_val = AsciiMetadataValue::from_str(&uuid)
            .map_err(|e| DriverCallError::Unknown(format!("invalid driver uuid {uuid:?}: {e}")))?;
        req.metadata_mut()
            .insert("x-jumpstarter-driver-uuid", uuid_val);
        let resp = grpc
            .server_streaming(req, path, jumpstarter_transport::demux::BytesCodec)
            .await
            .map_err(err_from_status)?;
        Ok(resp.into_inner())
    }

    /// Open a router byte stream to a driver `@exportstream`/resource handle. `request_json`
    /// is the `request` stream metadata (`{uuid, method}` for driver streams or `{uuid,
    /// x_jmp_content_encoding}` for resources). Returns a duplex [`ClientByteStream`] plus
    /// the resource initial metadata as JSON.
    pub async fn stream(&self, request_json: String) -> Result<Arc<ClientByteStream>, DriverCallError> {
        tracing::debug!(method = "Stream", request = %request_json, "rpc");
        let meta = AsciiMetadataValue::try_from(request_json)
            .map_err(|e| DriverCallError::InvalidArgument(e.to_string()))?;
        // Deep uplink buffer + large message limits so bulk resource/flash transfers pipeline
        // and aren't capped by tonic's 4 MiB default message size (allows multi-MiB chunks).
        let (tx, rx) = mpsc::channel::<StreamRequest>(256);
        let mut request = Request::new(ReceiverStream::new(rx));
        request.metadata_mut().insert("request", meta);
        let response = RouterServiceClient::with_interceptor(self.channel.clone(), self.auth())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024)
            .stream(request)
            .await
            .map_err(err_from_status)?;
        // Capture the allow-listed resource keys before consuming the response.
        let mut initial = serde_json::Map::new();
        for &key in &RELAY_KEYS {
            if let Some(value) = response.metadata().get(key).and_then(|v| v.to_str().ok()) {
                initial.insert(key.to_string(), serde_json::Value::String(value.to_string()));
            }
        }
        let initial_metadata = serde_json::Value::Object(initial).to_string();
        Ok(Arc::new(ClientByteStream {
            uplink: tx,
            downlink: Mutex::new(response.into_inner()),
            initial_metadata,
        }))
    }

    /// Signal the exporter to end the session early (runs afterLease).
    pub async fn end_session(&self) -> Result<bool, DriverCallError> {
        tracing::debug!(method = "EndSession", "rpc");
        let resp = self
            .exporter()
            .end_session(EndSessionRequest {})
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(resp.success)
    }

    /// `GetStatus` → JSON `{status, message, status_version, previous_status}` (status as
    /// the proto enum int; the Python status monitor maps it to `ExporterStatus`).
    pub async fn get_status(&self) -> Result<String, DriverCallError> {
        tracing::debug!(method = "GetStatus", "rpc");
        let resp = self
            .exporter()
            .get_status(GetStatusRequest {})
            .await
            .map_err(err_from_status)?
            .into_inner();
        let json = serde_json::json!({
            "status": resp.status,
            "message": resp.message,
            "status_version": resp.status_version,
            "previous_status": resp.previous_status,
        });
        Ok(json.to_string())
    }

    /// Open the exporter `LogStream` (hook + driver/system logs); pull entries as JSON.
    pub async fn log_stream(&self) -> Result<Arc<ClientLogStream>, DriverCallError> {
        tracing::debug!(method = "LogStream", "rpc");
        let stream = self
            .exporter()
            .log_stream(())
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(Arc::new(ClientLogStream {
            inner: Mutex::new(stream),
        }))
    }
}

/// A `LogStream` of hook + driver/system log entries, pulled JSON-at-a-time.
pub struct ClientLogStream {
    inner: Mutex<Streaming<LogStreamResponse>>,
}

impl ClientLogStream {
    /// Next log entry as JSON `{uuid, severity, message, source}`, or `None` at end.
    pub async fn next(&self) -> Result<Option<String>, DriverCallError> {
        let mut stream = self.inner.lock().await;
        match stream.next().await {
            Some(Ok(resp)) => {
                let json = serde_json::json!({
                    "uuid": resp.uuid,
                    "severity": resp.severity,
                    "message": resp.message,
                    "source": resp.source,
                });
                Ok(Some(json.to_string()))
            }
            Some(Err(status)) => Err(err_from_status(status)),
            None => Ok(None),
        }
    }
}

/// A boxed stream of already-decoded JSON results — the inner type of [`ClientResultStream`], so the
/// native and legacy producers can both target one concrete stream type.
type JsonResultStream = Pin<Box<dyn Stream<Item = Result<String, DriverCallError>> + Send>>;

/// A streaming-driver-call result stream, pulled JSON-at-a-time. The inner stream yields the
/// **already-decoded** JSON result per item, so the native (descriptor-decoded message bytes) and
/// legacy (`Value` codec) sources share one type and FFI surface — the producer in
/// [`ClientSession::streaming_driver_call`] does the per-item decode.
pub struct ClientResultStream {
    inner: Mutex<JsonResultStream>,
    /// `uuid/method` of the originating call, for stream-item logging.
    label: String,
}

impl ClientResultStream {
    /// Next JSON result, or `None` at end of stream.
    pub async fn next(&self) -> Result<Option<String>, DriverCallError> {
        let mut stream = self.inner.lock().await;
        match stream.next().await {
            Some(Ok(json)) => {
                tracing::trace!(stream = %self.label, "streaming_driver_call item");
                Ok(Some(json))
            }
            Some(Err(e)) => {
                tracing::debug!(stream = %self.label, error = %e, "streaming_driver_call stream error");
                Err(e)
            }
            None => {
                tracing::debug!(stream = %self.label, "streaming_driver_call stream EOF");
                Ok(None)
            }
        }
    }
}

/// A bidirectional router byte stream (driver `@exportstream` / resource). The Python
/// client reads/writes raw payloads; Rust owns the DATA/GOAWAY framing. The byte plane is plain
/// gRPC here — any shared-memory acceleration lives entirely in the hub↔driver-host hop.
///
/// Resource compression (gzip/xz/bz2/zstd) is NOT applied here: the client forwards the
/// (already-compressed) resource bytes verbatim, and the Rust host (`foreign.rs`) decompresses the
/// uplink / compresses the downlink so the language driver always sees RAW bytes. Keeping the codec
/// host-only means exactly one transform per byte path — a client-side mirror would double-transform
/// and deliver still-compressed bytes to the driver.
pub struct ClientByteStream {
    uplink: mpsc::Sender<StreamRequest>,
    downlink: Mutex<Streaming<StreamResponse>>,
    initial_metadata: String,
}

impl ClientByteStream {
    /// The resource initial metadata as a JSON object (`{}` for driver streams).
    pub fn initial_metadata(&self) -> String {
        self.initial_metadata.clone()
    }

    /// Next inbound payload, or `None` at EOF. The trailing `ABORTED "RouterStream:
    /// aclose"` the host emits on teardown is treated as a normal end-of-stream.
    pub async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
        let mut downlink = self.downlink.lock().await;
        loop {
            match downlink.next().await {
                Some(Ok(frame)) => match classify(frame) {
                    FrameAction::Payload(bytes) => return Ok(Some(bytes)),
                    FrameAction::Eof => return Ok(None),
                    FrameAction::Drop => continue,
                },
                Some(Err(status)) => {
                    if status.code() == Code::Aborted {
                        return Ok(None);
                    }
                    return Err(err_from_status(status));
                }
                None => return Ok(None),
            }
        }
    }

    /// Write one payload toward the driver (a DATA frame).
    pub async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
        self.uplink
            .send(data_frame(data))
            .await
            .map_err(|_| DriverCallError::Unknown("router stream closed".to_string()))
    }

    /// Half-close the uplink (the gRPC GOAWAY); the downlink stays open until the driver ends.
    pub async fn close(&self) -> Result<(), DriverCallError> {
        let _ = self.uplink.send(goaway_frame()).await;
        Ok(())
    }
}

#[cfg(test)]
mod native_unary_tests {
    use super::*;
    use jumpstarter_protocol::v1::{GetReportResponse, LogStreamResponse};
    use prost::Message as _;
    use jumpstarter_transport::demux::{Demux, SingleBackend};
    use jumpstarter_transport::transport::{connect_channel, InProcessTransport, Transport};
    use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};
    use std::sync::Arc;
    use tonic::metadata::MetadataMap;

    /// A backend whose `forward_unary` echoes `<uuid>|<path>|<body>` — proving the client
    /// `native_unary` carried the opaque message + the routing/auth metadata end-to-end.
    struct EchoBackend;

    #[tonic::async_trait]
    impl DriverBackend for EchoBackend {
        async fn get_report(&self) -> Result<GetReportResponse, Status> {
            Err(Status::unimplemented("echo"))
        }
        async fn open_router_stream(
            &self,
            _request_meta: AsciiMetadataValue,
            _uplink: FrameUplink,
        ) -> Result<RouterStreamOpen, Status> {
            Err(Status::unimplemented("echo"))
        }
        async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
            Err(Status::unimplemented("echo"))
        }
        async fn forward_unary(
            &self,
            path: &str,
            metadata: MetadataMap,
            body: bytes::Bytes,
        ) -> Result<(MetadataMap, bytes::Bytes, MetadataMap), Status> {
            let uuid = metadata
                .get("x-jumpstarter-driver-uuid")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_string();
            let mut out = format!("{uuid}|{path}|").into_bytes();
            out.extend_from_slice(&body);
            Ok((MetadataMap::new(), bytes::Bytes::from(out), MetadataMap::new()))
        }
    }

    /// Full client→server native loop: `ClientSession::native_unary` → demux (header routing) →
    /// `EchoBackend::forward_unary`, over the in-process transport.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn client_native_unary_round_trips_through_demux() {
        let transport = InProcessTransport::new();
        let incoming = transport.incoming();
        let demux = Demux::new(SingleBackend(Arc::new(EchoBackend)));
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(incoming)
                .await
        });

        let channel = connect_channel(&transport).await.expect("connect");
        let session = ClientSession {
            channel,
            passphrase: None,
            native: OnceCell::new(),
        };
        let path = "/jumpstarter.driver.power.v1.PowerInterface/On";
        let resp = session
            .native_unary("power-uuid-1".into(), path.into(), b"hello".to_vec())
            .await
            .expect("native_unary");
        assert_eq!(resp, format!("power-uuid-1|{path}|hello").into_bytes());
        server.abort();
    }

    // ---- native dispatch table (the client descriptor cache) -------------------------

    /// Build the exact self-contained `FileDescriptorSet` the Python host ships for
    /// `PowerInterface` (package `jumpstarter.interfaces.power.v1`): the interface file (importing
    /// `google/protobuf/empty.proto`) with `On`/`Off`/`SetVoltage`, plus the empty.proto dep,
    /// deps-first.
    fn power_descriptor_set_bytes() -> Vec<u8> {
        use prost_reflect::prost_types::field_descriptor_proto::{Label, Type};
        use prost_reflect::prost_types::{
            DescriptorProto, FieldDescriptorProto, FileDescriptorProto, FileDescriptorSet,
            MethodDescriptorProto, ServiceDescriptorProto,
        };

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
        let unary = |name: &str| MethodDescriptorProto {
            name: Some(name.into()),
            input_type: Some(".google.protobuf.Empty".into()),
            output_type: Some(".google.protobuf.Empty".into()),
            ..Default::default()
        };
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
        FileDescriptorSet {
            file: vec![empty_file, power_file],
        }
        .encode_to_vec()
    }

    /// The client cache indexes each `(uuid, @export-name)` to its native gRPC path + descriptors,
    /// resolving the import (empty.proto) and the lower_snake `@export` mapping.
    #[test]
    fn build_native_table_indexes_export_methods() {
        use jumpstarter_protocol::v1::DriverInstanceReport;

        let report = DriverInstanceReport {
            uuid: "power-1".into(),
            descriptor_set: Some(power_descriptor_set_bytes()),
            ..Default::default()
        };
        let table = build_native_table(&[report]);

        // On/Off/SetVoltage are all indexed under the instance uuid, by lower_snake export name.
        let on = table
            .get(&("power-1".to_string(), "on".to_string()))
            .expect("on route");
        assert_eq!(on.path, "/jumpstarter.interfaces.power.v1.PowerInterface/On");

        let sv = table
            .get(&("power-1".to_string(), "set_voltage".to_string()))
            .expect("set_voltage route");
        assert_eq!(
            sv.path,
            "/jumpstarter.interfaces.power.v1.PowerInterface/SetVoltage"
        );
        // Its input message is SetVoltageRequest with the int64 millivolts field.
        assert!(sv.input.get_field_by_name("millivolts").is_some());

        // A driver with no descriptor set yields no routes (legacy dispatch only).
        let bare = DriverInstanceReport {
            uuid: "bare-1".into(),
            descriptor_set: None,
            ..Default::default()
        };
        assert!(build_native_table(&[bare]).is_empty());
    }

    /// A full client-side round-trip with NO server: encode `set_voltage(12000)` against the cached
    /// input descriptor, then decode the (empty) response — proving the cache + encode/decode wiring
    /// the reimplemented `driver_call` relies on.
    #[test]
    fn cached_route_encodes_and_decodes_set_voltage() {
        use jumpstarter_protocol::v1::DriverInstanceReport;

        let report = DriverInstanceReport {
            uuid: "power-1".into(),
            descriptor_set: Some(power_descriptor_set_bytes()),
            ..Default::default()
        };
        let table = build_native_table(&[report]);
        let route = table
            .get(&("power-1".to_string(), "set_voltage".to_string()))
            .unwrap();

        // Client: args JSON -> request bytes (the body native_unary would carry).
        let body = encode_request(&route.input, "[12000]").unwrap();
        // Server would decode `body` to `{millivolts: 12000}`; here just assert it re-decodes.
        let echoed = crate::dynamic::decode_response(&route.input, &body).unwrap();
        assert_eq!(echoed, "12000"); // single-field unwrap

        // Client: empty (Empty) response -> null result.
        let result = decode_response(&route.output, &[]).unwrap();
        assert_eq!(result, "null");
    }
}
