//! The Rust-served `ExporterService` session server (native-migration inc1; design
//! `rust/docs/03-native-exporter-migration.md` §2–3).
//!
//! The Rust core terminates the client/hook-facing protocol itself: it serves
//! `ExporterService` on the per-process main + hook Unix sockets, owns `GetReport`
//! (the cached full-tree report) and the UUID routing table, and proxies the
//! driver-level RPCs (`DriverCall`/`StreamingDriverCall`/`LogStream`/`GetStatus`) to
//! the single slim Python host that owns the whole driver tree. A single host owns
//! every UUID, so `route` collapses Proxy duplicates exactly like `Session.mapping`.
//!
//! inc1 keeps the host process-lifetime and proxies the session-level RPCs verbatim;
//! later increments take `GetStatus`/`EndSession`/`LogStream` Rust-side (FSM-sourced
//! status, aggregated logs) and add per-lease host re-instantiation.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::exporter_service_server::{ExporterService, ExporterServiceServer};
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, EndSessionRequest, EndSessionResponse,
    GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse, ResetRequest,
    ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::net::UnixListener;
use tokio::task::JoinHandle;
use tokio_stream::wrappers::UnixListenerStream;
use tokio_stream::StreamExt as _;
use tonic::transport::{Channel, Server};
use tonic::{Request, Response, Status, Streaming};

use crate::Error;

/// Per-lease routing table: the cached full-tree `GetReport` plus the set of valid
/// driver UUIDs, all routing to the one slim host channel.
pub struct SessionRouter {
    host: Channel,
    driver_uuids: HashSet<String>,
    report: GetReportResponse,
}

impl SessionRouter {
    /// Build from the slim host's full-tree `GetReport`. The report and UUID set are
    /// cached (UUIDs are frozen for the host's lifetime — `metadata.py:7-10`); the
    /// envelope is kept verbatim from the host (the config-metadata envelope
    /// substitution is a deferred decision, design §3.2 / OQ3).
    pub async fn build(host: Channel) -> Result<Arc<Self>, Error> {
        let report = ExporterServiceClient::new(host.clone())
            .get_report(())
            .await?
            .into_inner();
        let driver_uuids = report.reports.iter().map(|r| r.uuid.clone()).collect();
        Ok(Arc::new(Self {
            host,
            driver_uuids,
            report,
        }))
    }

    /// The cached report (used for the controller `RegisterRequest`).
    pub fn report(&self) -> &GetReportResponse {
        &self.report
    }

    fn host_client(&self) -> ExporterServiceClient<Channel> {
        ExporterServiceClient::new(self.host.clone())
    }

    /// Validate a driver UUID at the boundary, returning the host client it routes
    /// to. Unknown/malformed UUID → `UNKNOWN`, matching `session.py:308` (the client
    /// distinguishes `NOT_FOUND`; see design §2.5 / OQ4).
    // `tonic::Status` is the natural error here — it propagates straight into the
    // ExporterService trait methods (which return it too).
    #[allow(clippy::result_large_err)]
    fn route(&self, uuid: &str) -> Result<ExporterServiceClient<Channel>, Status> {
        if self.driver_uuids.contains(uuid) {
            Ok(self.host_client())
        } else {
            Err(Status::unknown(format!("unknown driver uuid: {uuid}")))
        }
    }
}

/// Bind the `ExporterService` on the main and hook Unix sockets and serve until the
/// process exits. The listeners are bound synchronously (so the sockets exist on
/// return) and served on a background task.
pub fn serve(
    router: Arc<SessionRouter>,
    main_path: &Path,
    hook_path: &Path,
) -> Result<JoinHandle<()>, Error> {
    let main = UnixListener::bind(main_path)
        .map_err(|e| Error::Config(format!("binding main session socket: {e}")))?;
    let hook = UnixListener::bind(hook_path)
        .map_err(|e| Error::Config(format!("binding hook session socket: {e}")))?;

    // Separate sockets, one server: each accepted connection is independent, so the
    // hook/client SSL-frame isolation (session.py:244-257) is preserved.
    let incoming = UnixListenerStream::new(main).merge(UnixListenerStream::new(hook));
    let service = ExporterServiceServer::new(ExporterServer { router });

    Ok(tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_service(service)
            .serve_with_incoming(incoming)
            .await
        {
            tracing::error!(error = %e, "session server exited");
        }
    }))
}

/// The tonic `ExporterService` implementation over an [`Arc<SessionRouter>`].
struct ExporterServer {
    router: Arc<SessionRouter>,
}

#[tonic::async_trait]
impl ExporterService for ExporterServer {
    async fn get_report(&self, _req: Request<()>) -> Result<Response<GetReportResponse>, Status> {
        // Rust-owned: the cached full-tree report (no host round-trip).
        Ok(Response::new(self.router.report.clone()))
    }

    async fn driver_call(
        &self,
        req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        let req = req.into_inner();
        // Route by UUID, then forward the typed response / tonic Status unchanged —
        // the host owns marker lookup, Value marshaling, and exception mapping.
        self.router.route(&req.uuid)?.driver_call(req).await
    }

    type StreamingDriverCallStream = Streaming<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        let req = req.into_inner();
        let stream = self
            .router
            .route(&req.uuid)?
            .streaming_driver_call(req)
            .await?
            .into_inner();
        Ok(Response::new(stream))
    }

    type LogStreamStream = Streaming<LogStreamResponse>;
    async fn log_stream(
        &self,
        _req: Request<()>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        // inc1: proxy the single host's LogStream (inc3 aggregates Rust-side).
        let stream = self.router.host_client().log_stream(()).await?.into_inner();
        Ok(Response::new(stream))
    }

    async fn reset(&self, _req: Request<ResetRequest>) -> Result<Response<ResetResponse>, Status> {
        // Frozen quirk: Reset is UNIMPLEMENTED (jumpstarter.proto:142), used as a
        // client feature probe.
        Err(Status::unimplemented("Reset is not implemented"))
    }

    async fn get_status(
        &self,
        _req: Request<GetStatusRequest>,
    ) -> Result<Response<GetStatusResponse>, Status> {
        // inc1: proxy the host's status (forced LEASE_READY); inc3 sources it from
        // the lease FSM so it answers across leases.
        self.router
            .host_client()
            .get_status(GetStatusRequest {})
            .await
    }

    async fn end_session(
        &self,
        _req: Request<EndSessionRequest>,
    ) -> Result<Response<EndSessionResponse>, Status> {
        // inc1: no Rust-side lease wiring yet; inc3 triggers afterLease.
        Ok(Response::new(EndSessionResponse {
            success: false,
            message: Some("No active lease context".to_string()),
        }))
    }
}
