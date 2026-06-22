//! Client-side driver-call surface.
//!
//! Connects to a `JUMPSTARTER_HOST` transport socket and invokes drivers, mirroring the
//! Python `AsyncDriverClient`'s gRPC calls so the Python driver clients (and `j`) can
//! route through the Rust core (FFI) instead of grpcio + generated stubs. Rust owns the
//! value codec and the wire protocol; args/results cross as plain JSON. This is the
//! consumer mirror of [`crate::foreign::ForeignDriver`].

use std::sync::Arc;

use jumpstarter_client::exporter_logs::uds_channel;
use jumpstarter_protocol::router::{classify, data_frame, goaway_frame, FrameAction};
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{
    DriverCallRequest, EndSessionRequest, GetStatusRequest, LogStreamResponse, StreamRequest,
    StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::sync::{mpsc, Mutex};
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::AsciiMetadataValue;
use tonic::service::interceptor::InterceptedService;
use tonic::transport::{Channel, Endpoint};
use tonic::{Code, Request, Status, Streaming};

use crate::codec;
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

/// A connection to an exporter — either via its local `JUMPSTARTER_HOST` transport socket
/// (lease/local mode), or directly to a standalone exporter's TCP gRPC (`jmp shell --tls-grpc`).
pub struct ClientSession {
    channel: Channel,
    passphrase: Option<AsciiMetadataValue>,
}

impl ClientSession {
    /// Connect to the `JUMPSTARTER_HOST` the shell set: a UDS transport-socket path, or a bare
    /// `host:port` for a standalone exporter (direct mode). Direct mode reads
    /// `JMP_GRPC_INSECURE`/`JMP_GRPC_PASSPHRASE` from the env (`jmp shell --tls-grpc` sets them).
    pub async fn connect(host: String) -> Result<Self, DriverCallError> {
        // A UDS transport socket is a filesystem path; a direct target is a bare `host:port`.
        if host.contains('/') {
            let channel = uds_channel(host).await.map_err(DriverCallError::Unknown)?;
            return Ok(Self { channel, passphrase: None });
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
        let channel = Endpoint::from_shared(format!("http://{host}"))
            .map_err(|e| DriverCallError::Unknown(e.to_string()))?
            .connect()
            .await
            .map_err(|e| DriverCallError::Unknown(format!("connecting to direct exporter {host}: {e}")))?;
        let passphrase = std::env::var(jumpstarter_config::env::JMP_GRPC_PASSPHRASE)
            .ok()
            .filter(|p| !p.is_empty())
            .and_then(|p| AsciiMetadataValue::try_from(p).ok());
        Ok(Self { channel, passphrase })
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

    /// Invoke a unary driver call: `args_json` is a JSON array, returns the JSON result.
    pub async fn driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<String, DriverCallError> {
        let args = codec::json_args_to_values(&args_json)?;
        let resp = self
            .exporter()
            .driver_call(DriverCallRequest { uuid, method, args })
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(codec::value_result_to_json(&resp.result.unwrap_or_default())?)
    }

    /// Invoke a streaming driver call; results are pulled JSON-at-a-time from the returned
    /// [`ClientResultStream`].
    pub async fn streaming_driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<Arc<ClientResultStream>, DriverCallError> {
        let args = codec::json_args_to_values(&args_json)?;
        let stream = self
            .exporter()
            .streaming_driver_call(StreamingDriverCallRequest { uuid, method, args })
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(Arc::new(ClientResultStream {
            inner: Mutex::new(stream),
        }))
    }

    /// Open a router byte stream to a driver `@exportstream`/resource handle. `request_json`
    /// is the `request` stream metadata (`{uuid, method}` for driver streams or `{uuid,
    /// x_jmp_content_encoding}` for resources). Returns a duplex [`ClientByteStream`] plus
    /// the resource initial metadata as JSON.
    pub async fn stream(&self, request_json: String) -> Result<Arc<ClientByteStream>, DriverCallError> {
        let meta = AsciiMetadataValue::try_from(request_json)
            .map_err(|e| DriverCallError::InvalidArgument(e.to_string()))?;
        let (tx, rx) = mpsc::channel::<StreamRequest>(32);
        let mut request = Request::new(ReceiverStream::new(rx));
        request.metadata_mut().insert("request", meta);
        let response = RouterServiceClient::with_interceptor(self.channel.clone(), self.auth())
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

/// A streaming-driver-call result stream, pulled JSON-at-a-time.
pub struct ClientResultStream {
    inner: Mutex<Streaming<StreamingDriverCallResponse>>,
}

impl ClientResultStream {
    /// Next JSON result, or `None` at end of stream.
    pub async fn next(&self) -> Result<Option<String>, DriverCallError> {
        let mut stream = self.inner.lock().await;
        match stream.next().await {
            Some(Ok(resp)) => Ok(Some(codec::value_result_to_json(
                &resp.result.unwrap_or_default(),
            )?)),
            Some(Err(status)) => Err(err_from_status(status)),
            None => Ok(None),
        }
    }
}

/// A bidirectional router byte stream (driver `@exportstream` / resource). The Python
/// client reads/writes raw payloads; Rust owns the DATA/GOAWAY framing.
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

    /// Half-close the uplink (send GOAWAY); the downlink stays open until the driver ends.
    pub async fn close(&self) -> Result<(), DriverCallError> {
        let _ = self.uplink.send(goaway_frame()).await;
        Ok(())
    }
}
