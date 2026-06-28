//! The driver-call transport seam — `DriverBackend`.
//!
//! Also home to the native-gRPC [`Transport`](transport::Transport) trait + its variants
//! (in-process / SHM-ring duplex / network), the "tonic over swappable IO" core abstraction.
//!
//! A `DriverBackend` is *where a driver's calls are served*, abstracted over transport so the
//! exact same caller (the exporter's per-lease router on one side, a client session on the
//! other) drives a driver whether it lives:
//!
//! - in a **subprocess** reached over gRPC on a private UDS ([`ChannelBackend`]),
//! - at the **router** or a **direct exporter** over a TCP/UDS channel ([`ChannelBackend`]),
//! - or — later — **in-process** behind an FFI foreign `Driver` (jumpstarter-driver-core's
//!   `ForeignDriver`, which impls this trait).
//!
//! This is the **isometric** seam shared by both `jumpstarter-lease` and
//! `jumpstarter-exporter`: it depends only on the wire protocol + tonic, so a client can
//! consume `DriverBackend` without pulling in the exporter runtime. The trait is proto-typed
//! and tonic-facing on purpose — it is the transport/codec boundary; the binding-agnostic
//! per-driver `Driver` (JSON/bytes) is adapted *to* this trait by `ForeignDriver`, which is
//! where the value codec is applied.

pub mod demux;
pub mod transport;

use std::pin::Pin;
use std::str::FromStr;

use bytes::Bytes;

use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{
    GetReportResponse, LogStreamResponse, StreamRequest, StreamResponse,
};
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::{Stream, StreamExt as _};
use tonic::metadata::{AsciiMetadataValue, MetadataMap};
use tonic::transport::Channel;
use tonic::{Request, Status};
use tracing::{debug, trace};

/// A boxed server-streaming response of `T` items.
pub type ResponseStream<T> = Pin<Box<dyn Stream<Item = Result<T, Status>> + Send>>;

/// A client-uplink of router frames (forwarded to the driver verbatim). A concrete
/// `ReceiverStream` — not a boxed `dyn` — so it stays `Send + 'static` through the
/// `#[async_trait]` boundary (a boxed `dyn` loses `'static`, which tonic's `stream`
/// requires). The caller pumps client frames into the sender.
pub type FrameUplink = ReceiverStream<StreamRequest>;

/// Result of opening a router `Stream`: the driver's initial response metadata (the
/// resource handle JSON + `x_jmp_accept_encoding`, which the router tunnel filters and
/// relays) plus the downlink frame stream (including the trailing aclose status).
pub struct RouterStreamOpen {
    pub initial_metadata: MetadataMap,
    pub downlink: ResponseStream<StreamResponse>,
}

/// Where a driver's calls are served, abstracted over transport. The caller does no codec
/// here — the backend owns marker lookup, `Value` marshaling, exception mapping, and stream
/// semantics (a `ChannelBackend` forwards them over the wire; a `ForeignDriver` applies them
/// in-process).
#[tonic::async_trait]
pub trait DriverBackend: Send + Sync {
    /// The full-tree `GetReport` (cached by the caller for the lease lifetime).
    async fn get_report(&self) -> Result<GetReportResponse, Status>;

    /// Open a router `Stream` (driver `@exportstream` or resource handle): forward the
    /// client uplink + the `request` metadata to the driver, returning the driver's initial
    /// metadata and downlink. Frame boundaries are preserved 1:1.
    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status>;

    /// The driver/system `LogStream` (merged into the client log stream).
    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status>;

    /// Forward an **opaque** native per-driver unary gRPC call to this backend.
    ///
    /// This is the unary half of the native-gRPC demux: the core proxies a per-driver method
    /// (`jumpstarter.driver.*.v1.*`) it has **no proto knowledge of** — it never deserializes
    /// `body`, just relays the raw message bytes + metadata to the same method `path` on the
    /// backend and returns the response (initial metadata, message bytes, trailers) verbatim. It
    /// generalizes [`open_router_stream`](DriverBackend::open_router_stream)'s transparent proxy
    /// from `RouterService.Stream` to arbitrary unary methods. The default impl declines; a
    /// channel-backed host overrides it.
    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {
        let _ = (path, metadata, body);
        Err(Status::unimplemented(
            "native unary forwarding not supported by this backend",
        ))
    }

    /// Forward an **opaque** native per-driver **server-streaming** gRPC call to this backend.
    ///
    /// This is the streaming half of the native-gRPC demux. Because the demux is opaque (it has
    /// no proto knowledge and cannot tell unary from server-streaming apart from the path), it
    /// frames **every** native call as server-streaming on the wire — a unary method is just a
    /// one-message stream, which a unary client reads identically. So this method serves both:
    /// the default presents a [`forward_unary`](DriverBackend::forward_unary) as a one-item stream
    /// (correct for unary methods on a backend that only implements the unary half), and a
    /// genuinely server-streaming backend ([`ChannelBackend`], the core's dynamic backend)
    /// overrides it to relay N messages. Returns the initial metadata + the response message
    /// stream; tonic frames the trailing `grpc-status` itself.
    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {
        let (initial, message, _trailers) = self.forward_unary(path, metadata, body).await?;
        let stream: ResponseStream<Bytes> = Box::pin(tokio_stream::once(Ok(message)));
        Ok((initial, stream))
    }

    /// Forward an **opaque** native per-driver **client-/bidi-streaming** gRPC call — the fully
    /// general form of the native demux, and the method the demux always invokes.
    ///
    /// `uplink` is the inbound request-message stream and the return is the response-message stream.
    /// Because tonic frames all four gRPC call shapes identically at the HTTP/2 layer, this single
    /// primitive serves every one: a **unary** client sends one request frame and reads one response;
    /// **server-streaming** sends one and reads N; **client-streaming** sends N and reads one;
    /// **bidi** sends N and reads M.
    ///
    /// The default collapses to the single-message [`forward_stream`](DriverBackend::forward_stream)
    /// path — a unary or server-streaming client sends exactly one request frame, so it reads that
    /// frame and delegates (correct for the dynamic/foreign in-process backends, whose Python
    /// `@export` seam consumes no client stream). A backend that genuinely relays a client uplink to
    /// a real host ([`ChannelBackend`]) overrides this to forward **every** frame, so a native
    /// client-/bidi-streaming driver is proxied without truncation.
    async fn forward_bidi(
        &self,
        path: &str,
        metadata: MetadataMap,
        uplink: ResponseStream<Bytes>,
    ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {
        let mut uplink = uplink;
        let body = match uplink.next().await {
            Some(Ok(b)) => b,
            Some(Err(status)) => return Err(status),
            None => Bytes::new(),
        };
        self.forward_stream(path, metadata, body).await
    }
}

/// A [`DriverBackend`] backed by a tonic [`Channel`] to an `ExporterService` + `RouterService`
/// — a driver-host subprocess's private UDS, the router, or a direct exporter. A thin
/// forwarder: it serializes nothing of its own, relaying proto requests/responses verbatim.
pub struct ChannelBackend {
    channel: Channel,
}

impl ChannelBackend {
    pub fn new(channel: Channel) -> Self {
        Self { channel }
    }

    fn exporter(&self) -> ExporterServiceClient<Channel> {
        ExporterServiceClient::new(self.channel.clone())
    }

    /// The router-stream body as a plain inherent `async fn` — kept out of the
    /// `#[async_trait]` impl so its `Send` future is inferred normally (the boxed-`dyn`
    /// uplink otherwise trips async_trait's higher-ranked coercion).
    async fn open_router_stream_impl(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        let mut host_req = Request::new(uplink);
        host_req.metadata_mut().insert("request", request_meta);
        // 64 MiB encode/decode cap bounds the per-frame size on the router tunnel.
        let mut client = RouterServiceClient::new(self.channel.clone())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024);
        debug!(max_message_bytes = 64 * 1024 * 1024, "opening router stream");
        let host_resp = client.stream(host_req).await?;
        let initial_metadata = host_resp.metadata().clone();
        debug!(
            metadata_keys = initial_metadata.len(),
            "router stream opened; received initial metadata"
        );
        let downlink = Box::pin(host_resp.into_inner());
        Ok(RouterStreamOpen {
            initial_metadata,
            downlink,
        })
    }
}

#[tonic::async_trait]
impl DriverBackend for ChannelBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        trace!(rpc = "GetReport", "channel backend RPC dispatch");
        Ok(self.exporter().get_report(()).await?.into_inner())
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        trace!(rpc = "Stream", "channel backend RPC dispatch (router stream open)");
        // Forward uplink frames + the `request` metadata to the driver eagerly; it sends
        // its initial metadata before reading any frame, so this returns promptly.
        self.open_router_stream_impl(request_meta, uplink).await
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        trace!(rpc = "LogStream", "channel backend RPC dispatch");
        let stream = self.exporter().log_stream(()).await?.into_inner();
        Ok(Box::pin(stream))
    }

    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {
        trace!(rpc = "forward_unary", %path, "channel backend opaque native dispatch");
        // A raw tonic client over the same channel, carrying opaque proto via the identity
        // `BytesCodec` — no per-method types, so the core never decodes the per-driver message.
        let mut grpc = tonic::client::Grpc::new(self.channel.clone())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024);
        grpc.ready().await.map_err(|e| {
            Status::unavailable(format!("native forward: backend not ready: {e}"))
        })?;
        let path = http::uri::PathAndQuery::from_str(path)
            .map_err(|e| Status::internal(format!("native forward: bad method path: {e}")))?;
        // Carry the inbound metadata (driver uuid header included) verbatim to the backend method.
        let mut request = Request::new(body);
        *request.metadata_mut() = metadata;
        let response = grpc.unary(request, path, demux::BytesCodec).await?;
        // tonic's unary client path merges the backend's response trailers into the response
        // metadata (`client_streaming`: `parts.merge(trailers)`), so there is no separate trailer
        // map to split out here — the merged map is returned as `initial` and `trailers` is empty.
        // The demux re-emits `initial` on its outgoing response; tonic's server frames the
        // grpc-status trailer itself, so an `Ok` here proxies cleanly.
        let (initial, message, _extensions) = response.into_parts();
        Ok((initial, message, MetadataMap::new()))
    }

    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {
        trace!(rpc = "forward_stream", %path, "channel backend opaque native server-streaming dispatch");
        // The server-streaming analogue of `forward_unary`: a raw `server_streaming` client over
        // the same channel + `BytesCodec`, relaying the host's response message stream opaquely.
        // It also serves unary methods (the host sends one message), so the hub→host hop never
        // needs to know whether a method streams.
        let mut grpc = tonic::client::Grpc::new(self.channel.clone())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024);
        grpc.ready().await.map_err(|e| {
            Status::unavailable(format!("native forward: backend not ready: {e}"))
        })?;
        let path = http::uri::PathAndQuery::from_str(path)
            .map_err(|e| Status::internal(format!("native forward: bad method path: {e}")))?;
        let mut request = Request::new(body);
        *request.metadata_mut() = metadata;
        let response = grpc
            .server_streaming(request, path, demux::BytesCodec)
            .await?;
        let initial = response.metadata().clone();
        let stream: ResponseStream<Bytes> = Box::pin(response.into_inner());
        Ok((initial, stream))
    }

    async fn forward_bidi(
        &self,
        path: &str,
        metadata: MetadataMap,
        uplink: ResponseStream<Bytes>,
    ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {
        trace!(rpc = "forward_bidi", %path, "channel backend opaque native client/bidi-streaming dispatch");
        // The fully general analogue of `forward_stream`: a raw bidi `streaming` client over the same
        // channel + `BytesCodec`, relaying the client's request-frame stream to the host and the
        // host's response-frame stream back — opaquely. It subsumes the unary/server-streaming cases
        // (the client simply sends one request frame), so the hub→host hop never needs to know the
        // method's call shape.
        let mut grpc = tonic::client::Grpc::new(self.channel.clone())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024);
        grpc.ready().await.map_err(|e| {
            Status::unavailable(format!("native forward: backend not ready: {e}"))
        })?;
        let path = http::uri::PathAndQuery::from_str(path)
            .map_err(|e| Status::internal(format!("native forward: bad method path: {e}")))?;
        // tonic's streaming client wants a stream of bare encode messages; map the inbound request
        // stream to `Bytes`, stopping at the first uplink error (a client that aborted its uplink).
        let body = uplink.take_while(|r| r.is_ok()).filter_map(|r| r.ok());
        let mut request = Request::new(body);
        *request.metadata_mut() = metadata;
        let response = grpc.streaming(request, path, demux::BytesCodec).await?;
        let initial = response.metadata().clone();
        let stream: ResponseStream<Bytes> = Box::pin(response.into_inner());
        Ok((initial, stream))
    }
}

/// An opaque per-host guard, held for a host's lifetime and dropped to tear it down (a
/// subprocess is SIGKILLed on drop; an in-process host closes its driver tree). A marker
/// trait, *not* `Any` — callers only hold + drop it, never downcast.
pub trait HostGuard: Send {}
impl<T: Send> HostGuard for T {}
