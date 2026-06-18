//! The driver-host backend seam.
//!
//! `RoutingTable` ([`crate::session`]) routes a lease's driver calls and streams through
//! a `dyn DriverHostBackend` rather than a concrete gRPC channel, so the *same* Rust
//! exporter serves either the legacy out-of-process Python slim host
//! ([`SlimHostBackend`], gRPC over a private UDS) or — later — an in-process foreign host
//! (jumpstarter-core's `ForeignDriverHost`, calling Python/Kotlin/C through FFI).
//!
//! This trait is proto-typed and tonic-facing on purpose: it is the exporter's internal
//! seam, *not* the binding-agnostic facade. The facade's `ForeignHostApi`
//! (JSON/bytes-based) is adapted *to* this trait by a foreign backend, which is where the
//! value codec is applied. A single host owns the whole driver tree, so every UUID in a
//! lease routes to the one backend.

use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;

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

/// A boxed server-streaming response of `T` items.
pub type ResponseStream<T> = Pin<Box<dyn Stream<Item = Result<T, Status>> + Send>>;

/// A client-uplink of router frames (forwarded to the host verbatim). A concrete
/// `ReceiverStream` — not a boxed `dyn` — so it stays `Send + 'static` through the
/// `#[async_trait]` boundary (a boxed `dyn` loses `'static`, which tonic's `stream`
/// requires). The caller ([`crate::tunnel`]) pumps client frames into the sender.
pub type FrameUplink = ReceiverStream<StreamRequest>;

/// Result of opening a router `Stream`: the host's initial response metadata (the
/// resource handle JSON + `x_jmp_accept_encoding`, which [`crate::tunnel`] filters and
/// relays) plus the downlink frame stream (including the trailing aclose status).
pub struct RouterStreamOpen {
    pub initial_metadata: MetadataMap,
    pub downlink: ResponseStream<StreamResponse>,
}

/// A lease's driver host, abstracted over transport. The exporter does no codec here —
/// the host owns marker lookup, `Value` marshaling, exception mapping, and stream
/// semantics — exactly as the pre-seam gRPC proxy did.
#[tonic::async_trait]
pub trait DriverHostBackend: Send + Sync {
    /// The full-tree `GetReport` (cached by `RoutingTable` for the lease lifetime).
    async fn get_report(&self) -> Result<GetReportResponse, Status>;

    /// Invoke a unary driver call.
    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status>;

    /// Invoke a server-streaming driver call.
    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status>;

    /// Open a router `Stream` (driver `@exportstream` or resource handle): forward the
    /// client uplink + the `request` metadata to the host, returning the host's initial
    /// metadata and downlink. Frame boundaries are preserved 1:1.
    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status>;

    /// The host's driver/system `LogStream` (merged into the client log stream).
    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status>;
}

/// The legacy backend: the slim Python host reached over gRPC on a private UDS channel.
/// A thin forwarder that preserves the exact behavior of the pre-seam proxy.
pub struct SlimHostBackend {
    channel: Channel,
}

impl SlimHostBackend {
    pub fn new(channel: Channel) -> Self {
        Self { channel }
    }

    fn exporter(&self) -> ExporterServiceClient<Channel> {
        ExporterServiceClient::new(self.channel.clone())
    }

    /// The router-stream body as a plain inherent `async fn` — kept out of the
    /// `#[async_trait]` impl so its `Send` future is inferred normally (the boxed-`dyn`
    /// uplink otherwise trips async_trait's higher-ranked coercion).
    async fn slim_open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        let mut host_req = Request::new(uplink);
        host_req.metadata_mut().insert("request", request_meta);
        let mut client = RouterServiceClient::new(self.channel.clone());
        let host_resp = client.stream(host_req).await?;
        let initial_metadata = host_resp.metadata().clone();
        let downlink = Box::pin(host_resp.into_inner());
        Ok(RouterStreamOpen {
            initial_metadata,
            downlink,
        })
    }
}

#[tonic::async_trait]
impl DriverHostBackend for SlimHostBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        Ok(self.exporter().get_report(()).await?.into_inner())
    }

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        Ok(self.exporter().driver_call(req).await?.into_inner())
    }

    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        let stream = self.exporter().streaming_driver_call(req).await?.into_inner();
        Ok(Box::pin(stream))
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        // Forward uplink frames + the `request` metadata to the host eagerly; it sends
        // its initial metadata before reading any frame, so this returns promptly.
        self.slim_open_router_stream(request_meta, uplink).await
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        let stream = self.exporter().log_stream(()).await?.into_inner();
        Ok(Box::pin(stream))
    }
}

/// An opaque per-lease handle held alive for the lease and dropped at lease end to tear
/// the host down (the `SlimHost` subprocess is SIGKILLed on drop; a foreign host's guard
/// closes its driver tree).
pub type HostGuard = Box<dyn std::any::Any + Send>;

/// Produces a fresh driver host for each lease. The exporter is generic over this so the
/// *same* lease loop drives either the out-of-process slim host ([`SlimHostFactory`]) or
/// an in-process foreign host (jumpstarter-core's foreign factory).
#[tonic::async_trait]
pub trait HostFactory: Send + Sync + 'static {
    /// Provision a fresh host: a backend to route a lease's calls into, plus a guard held
    /// for the lease lifetime. A fresh tree per lease (fresh drivers) is the contract.
    async fn provision(&self) -> Result<(Arc<dyn DriverHostBackend>, HostGuard), crate::Error>;
}

/// The legacy factory: spawn a [`crate::driver_host::SlimHost`] subprocess and route into
/// it over its private UDS channel. The `SlimHost` is the lease guard (drop = SIGKILL).
pub struct SlimHostFactory {
    config_path: PathBuf,
}

impl SlimHostFactory {
    pub fn new(config_path: PathBuf) -> Self {
        Self { config_path }
    }
}

#[tonic::async_trait]
impl HostFactory for SlimHostFactory {
    async fn provision(&self) -> Result<(Arc<dyn DriverHostBackend>, HostGuard), crate::Error> {
        let host = crate::driver_host::SlimHost::spawn(&self.config_path).await?;
        let channel = crate::control::uds_channel(host.socket()).await?;
        let backend: Arc<dyn DriverHostBackend> = Arc::new(SlimHostBackend::new(channel));
        Ok((backend, Box::new(host)))
    }
}
