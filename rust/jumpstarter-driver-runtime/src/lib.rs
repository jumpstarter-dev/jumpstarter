//! The generic, codegen-free driver host.
//!
//! A proto-first Jumpstarter driver is authored as a **stock `tonic` service** — the author
//! implements the `tonic-build`-generated service trait (`impl PowerInterface for MockPower`) and
//! nothing else. [`serve_driver`] takes that service (its `*Server<T>` form) and serves it over the
//! Jumpstarter **SHM transport** ([`ShmTransport`] — the hub↔driver-host hop, the proper
//! high-performance channel), alongside a minimal `ExporterService` that advertises the interface
//! descriptor over `GetReport`. It returns the hub-side view of that SHM hop as the **existing
//! generic [`ChannelBackend`]**, so:
//!
//! ```text
//!   client ──plain gRPC──▶ exporter{GetReport + Demux} ──ChannelBackend over SHM──▶ serve_driver host
//!                                                                                   { ExporterService + tonic service }
//! ```
//!
//! There is **no per-interface generated adapter**: `tonic` itself decodes/dispatches the typed
//! request on the host, the SHM transport + `ChannelBackend` forward opaque frames, and this one
//! generic runtime serves any interface. The only generated code in the whole system is the typed
//! *client* (which the consumer uses); the driver side is stock `tonic` + this runtime.

use std::collections::HashMap;
use std::convert::Infallible;
use std::pin::Pin;
use std::sync::Arc;

use jumpstarter_protocol::v1::exporter_service_server::{ExporterService, ExporterServiceServer};
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, DriverInstanceReport, EndSessionRequest,
    EndSessionResponse, GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse,
    ResetRequest, ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use jumpstarter_transport::transport::{connect_channel, ShmTransport, Transport};
use jumpstarter_transport::{ChannelBackend, DriverBackend};
use tonic::transport::Server;
use tonic::{Request, Response, Status};

const CLIENT_LABEL: &str = "jumpstarter.dev/client";
const NAME_LABEL: &str = "jumpstarter.dev/name";

type RespStream<T> = Pin<Box<dyn tokio_stream::Stream<Item = Result<T, Status>> + Send>>;

/// Serve `service` (a stock `tonic` `*Server<T>`) as a Jumpstarter driver host over the SHM
/// transport, returning the hub-side [`DriverBackend`] (a [`ChannelBackend`] over the SHM hop).
///
/// - `name` is the driver instance's `jumpstarter.dev/name` label (the accessor a client resolves).
/// - `client_class` is the `jumpstarter.dev/client` label (the client class that drives it).
/// - `descriptor_set` is the interface's serialized `FileDescriptorSet` — `tonic-build`'s
///   `FILE_DESCRIPTOR_SET`, the single descriptor source of truth — advertised over `GetReport`.
/// - `service` is the author's typed service wrapped in its generated server, e.g.
///   `PowerInterfaceServer::new(MockPower::default())`.
///
/// The host runs detached on the current tokio runtime; the returned backend owns the SHM channel
/// that keeps it alive.
pub async fn serve_driver<S>(
    name: &str,
    client_class: &str,
    descriptor_set: Vec<u8>,
    service: S,
) -> std::io::Result<Arc<dyn DriverBackend>>
where
    S: tower::Service<
            http::Request<tonic::body::BoxBody>,
            Response = http::Response<tonic::body::BoxBody>,
            Error = Infallible,
        > + tonic::server::NamedService
        + Clone
        + Send
        + Sync
        + 'static,
    S::Future: Send + 'static,
{
    let uuid = uuid::Uuid::new_v4().to_string();
    let report = GetReportResponse {
        reports: vec![DriverInstanceReport {
            uuid,
            parent_uuid: None,
            labels: HashMap::from([
                (CLIENT_LABEL.to_string(), client_class.to_string()),
                (NAME_LABEL.to_string(), name.to_string()),
            ]),
            description: None,
            methods_description: HashMap::new(),
            // The single descriptor source of truth — the same FileDescriptorSet tonic-build emits.
            descriptor_set: Some(descriptor_set),
        }],
        ..Default::default()
    };

    // The hub↔driver-host hop: a fresh SHM ring duplex. tonic runs over it exactly as over a socket.
    let shm = ShmTransport::new()?;
    let incoming = shm.incoming();
    let exporter = ExporterServiceServer::new(ReportOnlyExporter { report });

    tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_service(exporter) // GetReport: advertises the descriptor.
            .add_service(service) // the author's typed interface: tonic decodes/dispatches.
            .serve_with_incoming(incoming)
            .await
        {
            tracing::warn!(error = %e, "driver host server exited");
        }
    });

    // Dial the host over the same SHM transport; the resulting tonic Channel keeps a clone of the
    // transport (and thus the rings) alive, so the local `shm` may drop. The ChannelBackend is the
    // generic, interface-agnostic DriverBackend the hub/exporter forwards through.
    let channel = connect_channel(&shm).await.map_err(std::io::Error::other)?;
    Ok(Arc::new(ChannelBackend::new(channel)))
}

/// A minimal `ExporterService` for a single driver host: it answers `GetReport` with the host's one
/// driver-instance report (carrying the interface descriptor) and declines everything else — a
/// proto-first host serves its interface as native gRPC, not through the legacy `DriverCall` codec.
struct ReportOnlyExporter {
    report: GetReportResponse,
}

#[tonic::async_trait]
impl ExporterService for ReportOnlyExporter {
    async fn get_report(&self, _req: Request<()>) -> Result<Response<GetReportResponse>, Status> {
        Ok(Response::new(self.report.clone()))
    }

    async fn driver_call(
        &self,
        _req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    type StreamingDriverCallStream = RespStream<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        _req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    type LogStreamStream = RespStream<LogStreamResponse>;
    async fn log_stream(
        &self,
        _req: Request<()>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn reset(
        &self,
        _req: Request<ResetRequest>,
    ) -> Result<Response<ResetResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn get_status(
        &self,
        _req: Request<GetStatusRequest>,
    ) -> Result<Response<GetStatusResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn end_session(
        &self,
        _req: Request<EndSessionRequest>,
    ) -> Result<Response<EndSessionResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }
}
