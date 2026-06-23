//! The inner `RouterService.Stream` gRPC proxy (native-migration inc2; design §5).
//!
//! The Rust core serves `RouterService.Stream` to clients — driver `@exportstream`
//! streams (consoles) and resource handles (flash/dump) — and transparently proxies
//! it to the slim host that owns the driver tree. It:
//!
//! - forwards whole `StreamRequest`/`StreamResponse` messages **verbatim** (1:1, no
//!   re-chunking), preserving frame boundaries and the `GOAWAY`=EOF vs zero-length
//!   `DATA` distinction;
//! - relays the host's initial response metadata (the `resource` handle JSON +
//!   `x_jmp_accept_encoding`) **before** any downlink frame, so a resource client's
//!   `initial_metadata()` handshake never deadlocks (`core.py:450-452`); and
//! - lets the host's trailing status — the `ABORTED "RouterStream: aclose"` teardown
//!   (`streams/router.py:65-69`) — pass straight through, held open until the client
//!   half-closes (the host owns the lifetime via its done-callback, `session.py:325`).
//!
//! Rust does no codec / resource-handle / progress work; the host owns all stream
//! semantics (design §5.4).
//!
//! Lifetime and backpressure are handled by the transparent passthrough itself, not
//! an explicit relay loop:
//! - **Cancellation** is client-driven via `Drop`: when the client cancels, tonic
//!   drops the returned `Streaming`, which drops the host call (cancelling it) and
//!   the uplink it owns. When the *host* tears down, its trailing
//!   `ABORTED "RouterStream: aclose"` status propagates straight through.
//! - **Backpressure** is HTTP/2 flow control end to end: the proxy never buffers
//!   between the two calls (each direction is pull-driven by the downstream peer), so
//!   a slow client throttles the host and vice versa. Verified: a 500 MB resource
//!   upload holds the exporter at ~3 MB of growth, so the bounded(32) memory pipe the
//!   Python path uses (`streams/common.py`) is unnecessary here.

use std::sync::Arc;

use jumpstarter_protocol::router::{classify, data_frame, goaway_frame, FrameAction};
use jumpstarter_protocol::v1::router_service_server::RouterService;
use jumpstarter_protocol::v1::{StreamRequest, StreamResponse};
use jumpstarter_shm::bridge::{RingReader, RingWriter};
use jumpstarter_shm::wire::{RING_CAP, RING_CHUNK, SHM_DOWN_KEY, SHM_UP_KEY};
use jumpstarter_shm::Ring;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataKey, MetadataMap};
use tonic::{Request, Response, Status, Streaming};

use crate::backend::ResponseStream;
use crate::session::SharedSession;

/// Metadata keys the host emits on a resource Stream's initial response
/// (`driver/base.py:189-198`). Only these are relayed, so the client's
/// `ResourceMetadata(**dict(initial_metadata()))` parses without extra keys
/// (`client/core.py:452`); driver streams emit none.
const RELAY_KEYS: [&str; 2] = ["resource", "x_jmp_accept_encoding"];

/// The tonic `RouterService` implementation: a transparent bidi proxy to the host.
pub struct RouterServer {
    shared: Arc<SharedSession>,
}

impl RouterServer {
    pub fn new(shared: Arc<SharedSession>) -> Self {
        Self { shared }
    }
}

#[tonic::async_trait]
impl RouterService for RouterServer {
    type StreamStream = ResponseStream<StreamResponse>;

    async fn stream(
        &self,
        request: Request<Streaming<StreamRequest>>,
    ) -> Result<Response<Self::StreamStream>, Status> {
        // 1. The `request` invocation metadata carries a JSON StreamRequest
        //    (`common/streams.py:14-33`). Validate the target uuid at the boundary
        //    (unknown/malformed -> UNKNOWN, §2.5); the host re-parses the rest.
        let request_meta = request
            .metadata()
            .get("request")
            .cloned()
            .ok_or_else(|| Status::unknown("missing `request` stream metadata"))?;
        // A shared-memory uplink (set only by the hub's `ShmChannelBackend`): when present, the
        // bulk uplink bytes arrive via the named ring, not the gRPC DATA frames.
        let shm_up = request
            .metadata()
            .get(SHM_UP_KEY)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        let shm_active = shm_up.is_some();
        let uuid = parse_uuid(&request_meta)?;
        let routing = self
            .shared
            .routing()
            .ok_or_else(|| Status::unknown("no active lease"))?;
        if !routing.knows_uuid(&uuid) {
            return Err(Status::unknown(format!("unknown driver uuid: {uuid}")));
        }

        // 2. Feed the backend's uplink. Normally we pump the client gRPC DATA frames into a bounded
        //    channel (backpressure: a slow host fills it → HTTP/2 throttles the client). In SHM mode
        //    the bytes come from the ring instead; a reader thread drains it into DATA frames and
        //    emits GOAWAY at EOF, so everything downstream (`ForeignDriver`) is unchanged.
        let (tx, rx) = tokio::sync::mpsc::channel::<StreamRequest>(256);
        if let Some(path) = shm_up {
            let ring = Ring::open(std::path::Path::new(&path), RING_CAP)
                .map_err(|e| Status::internal(format!("shm ring open: {e}")))?;
            // Unlink immediately: the mmap keeps the region alive, so the ring file never leaks on
            // teardown (graceful or crash) once both ends have mapped it.
            let _ = std::fs::remove_file(&path);
            let mut reader = RingReader::spawn(ring, RING_CHUNK, 8);
            tokio::spawn(async move {
                while let Some(chunk) = reader.recv().await {
                    if tx.send(data_frame(chunk)).await.is_err() {
                        return;
                    }
                }
                let _ = tx.send(goaway_frame()).await; // EOF
            });
        } else {
            let mut frames = request.into_inner();
            tokio::spawn(async move {
                while let Some(Ok(frame)) = frames.next().await {
                    if tx.send(frame).await.is_err() {
                        break;
                    }
                }
            });
        }

        // 3. Open the host Stream eagerly via the backend. The host sends its initial
        //    metadata before reading any frame (`session.py:324`), so this returns
        //    promptly with the resource handle / encoding — no metadata-before-frame
        //    deadlock.
        let opened = routing
            .backend()
            .open_router_stream(request_meta, ReceiverStream::new(rx))
            .await?;

        // 4. Re-emit the host's resource keys on the client-facing response, then handle the
        //    downlink. Three cases:
        //    - non-SHM: relay the host downlink straight through (DATA + trailing status).
        //    - SHM leaf (host: backend produced the bytes, no downlink ring yet): create a downlink
        //      ring, divert the driver's DATA into it, and return only the trailing status on gRPC.
        //    - SHM relay (hub: the host already made a downlink ring): pass the gRPC trailing-status
        //      stream through and relay the ring key so the client reads the ring directly.
        let has_downlink_ring = opened.initial_metadata.get(SHM_DOWN_KEY).is_some();
        if shm_active && !has_downlink_ring {
            // Leaf: divert the driver downlink into a ring; gRPC carries only the trailing status.
            let path =
                std::env::temp_dir().join(format!("jmp-shm-down-{}.d", uuid::Uuid::new_v4()));
            let ring = Ring::create(&path, RING_CAP)
                .map_err(|e| Status::internal(format!("shm downlink ring create: {e}")))?;
            let mut writer = RingWriter::spawn(ring, 8);
            let (status_tx, status_rx) =
                tokio::sync::mpsc::channel::<Result<StreamResponse, Status>>(1);
            let mut downlink = opened.downlink;
            tokio::spawn(async move {
                while let Some(item) = downlink.next().await {
                    match item {
                        Ok(frame) => match classify(frame) {
                            FrameAction::Payload(p) => {
                                if writer.send(p).await.is_err() {
                                    break;
                                }
                            }
                            FrameAction::Eof => break,
                            FrameAction::Drop => {}
                        },
                        // The trailing ABORTED "aclose" (or any error) → forward over gRPC; the
                        // client treats it as EOF once it has drained the ring.
                        Err(status) => {
                            let _ = status_tx.send(Err(status)).await;
                            break;
                        }
                    }
                }
                writer.close(); // ring EOF (close flag) — the client's reader sees end-of-data
            });
            let downlink: ResponseStream<StreamResponse> =
                Box::pin(tokio_stream::wrappers::ReceiverStream::new(status_rx));
            let mut response = Response::new(downlink);
            relay_metadata(&opened.initial_metadata, response.metadata_mut());
            if let Ok(val) = AsciiMetadataValue::try_from(path.to_string_lossy().as_ref()) {
                response
                    .metadata_mut()
                    .insert(MetadataKey::from_static(SHM_DOWN_KEY), val);
            }
            Ok(response)
        } else {
            let mut response = Response::new(opened.downlink);
            relay_metadata(&opened.initial_metadata, response.metadata_mut());
            // Relay the downlink ring key (hub → client) so the client reads the ring directly.
            if let Some(val) = opened.initial_metadata.get(SHM_DOWN_KEY) {
                response
                    .metadata_mut()
                    .insert(MetadataKey::from_static(SHM_DOWN_KEY), val.clone());
            }
            Ok(response)
        }
    }
}

/// Extract the `uuid` from the JSON `request` metadata value.
// `tonic::Status` is the natural error — it propagates into the RouterService method.
#[allow(clippy::result_large_err)]
fn parse_uuid(meta: &AsciiMetadataValue) -> Result<String, Status> {
    let raw = meta
        .to_str()
        .map_err(|_| Status::unknown("malformed `request` stream metadata"))?;
    let value: serde_json::Value = serde_json::from_str(raw)
        .map_err(|_| Status::unknown("malformed `request` stream metadata"))?;
    value
        .get("uuid")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| Status::unknown("missing uuid in `request` stream metadata"))
}

/// Copy the allow-listed resource keys from the host's initial metadata, byte for
/// byte (including `x_jmp_accept_encoding`'s empty-string decline case — never
/// derived, defaulted, or omitted; design §5.2).
fn relay_metadata(host: &MetadataMap, out: &mut MetadataMap) {
    for &key in &RELAY_KEYS {
        if let (Some(value), Ok(key)) = (host.get(key), MetadataKey::from_bytes(key.as_bytes())) {
            out.insert(key, value.clone());
        }
    }
}
