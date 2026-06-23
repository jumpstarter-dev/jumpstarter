//! The driver-call transport seam — `DriverBackend`.
//!
//! A `DriverBackend` is *where a driver's calls are served*, abstracted over transport so the
//! exact same caller (the exporter's per-lease router on one side, a client session on the
//! other) drives a driver whether it lives:
//!
//! - in a **subprocess** reached over gRPC on a private UDS ([`ChannelBackend`]),
//! - at the **router** or a **direct exporter** over a TCP/UDS channel ([`ChannelBackend`]),
//! - or — later — **in-process** behind an FFI foreign `Driver` (jumpstarter-core's
//!   `ForeignDriver`, which impls this trait).
//!
//! This is the **isometric** seam shared by both `jumpstarter-client` and
//! `jumpstarter-exporter`: it depends only on the wire protocol + tonic, so a client can
//! consume `DriverBackend` without pulling in the exporter runtime. The trait is proto-typed
//! and tonic-facing on purpose — it is the transport/codec boundary; the binding-agnostic
//! per-driver `Driver` (JSON/bytes) is adapted *to* this trait by `ForeignDriver`, which is
//! where the value codec is applied.

use std::pin::Pin;

use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, GetReportResponse, LogStreamResponse, StreamRequest,
    StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::Stream;
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

    /// Invoke a unary driver call.
    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status>;

    /// Invoke a server-streaming driver call.
    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status>;

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

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        trace!(rpc = "DriverCall", uuid = %req.uuid, method = %req.method, "channel backend RPC dispatch");
        Ok(self.exporter().driver_call(req).await?.into_inner())
    }

    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        trace!(rpc = "StreamingDriverCall", uuid = %req.uuid, method = %req.method, "channel backend RPC dispatch");
        let stream = self.exporter().streaming_driver_call(req).await?.into_inner();
        Ok(Box::pin(stream))
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
}

/// An opaque per-host guard, held for a host's lifetime and dropped to tear it down (a
/// subprocess is SIGKILLed on drop; an in-process host closes its driver tree). A marker
/// trait, *not* `Any` — callers only hold + drop it, never downcast.
pub trait HostGuard: Send {}
impl<T: Send> HostGuard for T {}
