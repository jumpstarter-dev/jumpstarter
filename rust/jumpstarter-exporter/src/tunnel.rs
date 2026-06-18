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

use jumpstarter_protocol::v1::router_service_server::RouterService;
use jumpstarter_protocol::v1::{StreamRequest, StreamResponse};
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
        let uuid = parse_uuid(&request_meta)?;
        let routing = self
            .shared
            .routing()
            .ok_or_else(|| Status::unknown("no active lease"))?;
        if !routing.knows_uuid(&uuid) {
            return Err(Status::unknown(format!("unknown driver uuid: {uuid}")));
        }

        // 2. Pump the client uplink frames into a bounded channel the backend reads as a
        //    `ReceiverStream`, ending on the client's half-close / error. The bounded(32)
        //    channel preserves backpressure: when the host is slow the channel fills, the
        //    pump stops reading, and HTTP/2 flow control throttles the client.
        let (tx, rx) = tokio::sync::mpsc::channel::<StreamRequest>(32);
        let mut frames = request.into_inner();
        tokio::spawn(async move {
            while let Some(Ok(frame)) = frames.next().await {
                if tx.send(frame).await.is_err() {
                    break;
                }
            }
        });

        // 3. Open the host Stream eagerly via the backend. The host sends its initial
        //    metadata before reading any frame (`session.py:324`), so this returns
        //    promptly with the resource handle / encoding — no metadata-before-frame
        //    deadlock.
        let opened = routing
            .backend()
            .open_router_stream(request_meta, ReceiverStream::new(rx))
            .await?;

        // 4. Re-emit only the host's resource keys on the client-facing response (set
        //    before returning, so they precede any downlink frame), then relay the
        //    host downlink — including its trailing ABORTED "RouterStream: aclose"
        //    teardown — straight through.
        let mut response = Response::new(opened.downlink);
        relay_metadata(&opened.initial_metadata, response.metadata_mut());
        Ok(response)
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
