//! The Rust-served `ExporterService` session server (native-migration inc1–inc3;
//! design `rust/docs/03-native-exporter-migration.md` §2–4).
//!
//! The server is **process-lifetime** — bound once on the main + hook Unix sockets —
//! but its driver routing is **per-lease**: a fresh slim host owns each lease's tree,
//! so the [`RoutingTable`] (host channel + cached `GetReport` + valid UUIDs) is
//! swapped in at lease start and cleared at lease end. `GetStatus`/`EndSession` are
//! answered from process-lifetime state (the lease FSM's [`StatusSnapshot`] and the
//! current lease's early-end signal), so they work *across* leases; driver calls and
//! streams route into the current host, returning `UNKNOWN` when idle.

use std::collections::HashSet;
use std::path::Path;
use std::pin::Pin;
use std::sync::Arc;

use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::exporter_service_server::{ExporterService, ExporterServiceServer};
use jumpstarter_protocol::v1::router_service_server::RouterServiceServer;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, EndSessionRequest, EndSessionResponse,
    GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse, ResetRequest,
    ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::net::UnixListener;
use tokio::sync::{watch, Notify};
use tokio::task::JoinHandle;
use tokio_stream::wrappers::UnixListenerStream;
use tokio_stream::{Stream, StreamExt as _};
use tonic::transport::{Channel, Server};
use tonic::{Request, Response, Status, Streaming};

use crate::control::StatusSnapshot;
use crate::Error;

/// A single lease's driver routing: the slim host channel, its cached full-tree
/// `GetReport`, and the set of valid driver UUIDs (all routing to the one host; a
/// single host owns the whole tree, so Proxy duplicates collapse like
/// `Session.mapping`).
pub struct RoutingTable {
    host: Channel,
    driver_uuids: HashSet<String>,
    report: GetReportResponse,
}

impl RoutingTable {
    /// Build from the slim host's full-tree `GetReport` (cached for the lease — UUIDs
    /// are frozen for the host's lifetime, `metadata.py:7-10`).
    pub async fn build(host: Channel) -> Result<Self, Error> {
        let report = ExporterServiceClient::new(host.clone())
            .get_report(())
            .await?
            .into_inner();
        let driver_uuids = report.reports.iter().map(|r| r.uuid.clone()).collect();
        Ok(Self {
            host,
            driver_uuids,
            report,
        })
    }

    /// The cached report (used for the controller `RegisterRequest` and `GetReport`).
    pub fn report(&self) -> &GetReportResponse {
        &self.report
    }

    /// The channel to the slim host (for the [`crate::tunnel`] RouterService proxy).
    pub(crate) fn host_channel(&self) -> Channel {
        self.host.clone()
    }

    /// Whether `uuid` is a known driver instance in this lease.
    pub(crate) fn knows_uuid(&self, uuid: &str) -> bool {
        self.driver_uuids.contains(uuid)
    }

    fn host_client(&self) -> ExporterServiceClient<Channel> {
        ExporterServiceClient::new(self.host.clone())
    }

    /// Validate a driver UUID, returning the host client it routes to. Unknown UUID →
    /// `UNKNOWN`, matching `session.py:308` (the client distinguishes `NOT_FOUND`; §2.5).
    #[allow(clippy::result_large_err)]
    fn route(&self, uuid: &str) -> Result<ExporterServiceClient<Channel>, Status> {
        if self.driver_uuids.contains(uuid) {
            Ok(self.host_client())
        } else {
            Err(Status::unknown(format!("unknown driver uuid: {uuid}")))
        }
    }
}

/// Process-lifetime session state shared by every RPC handler. The `watch` channels
/// are driven by the lease loop: `routing` is swapped per lease (`None` when idle),
/// `status` tracks the FSM, and `end_session` holds the current lease's early-end
/// signal.
pub struct SharedSession {
    routing: watch::Receiver<Option<Arc<RoutingTable>>>,
    status: watch::Receiver<StatusSnapshot>,
    end_session: watch::Receiver<Option<Arc<Notify>>>,
}

impl SharedSession {
    pub fn new(
        routing: watch::Receiver<Option<Arc<RoutingTable>>>,
        status: watch::Receiver<StatusSnapshot>,
        end_session: watch::Receiver<Option<Arc<Notify>>>,
    ) -> Arc<Self> {
        Arc::new(Self {
            routing,
            status,
            end_session,
        })
    }

    pub(crate) fn routing(&self) -> Option<Arc<RoutingTable>> {
        self.routing.borrow().clone()
    }

    #[allow(clippy::result_large_err)]
    fn require_routing(&self) -> Result<Arc<RoutingTable>, Status> {
        self.routing()
            .ok_or_else(|| Status::unknown("no active lease"))
    }
}

/// Bind the `ExporterService` + `RouterService` on the main and hook Unix sockets and
/// serve for the process lifetime. The listeners are bound synchronously (so the
/// sockets exist on return) and served on a background task.
pub fn serve(
    shared: Arc<SharedSession>,
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
    let exporter = ExporterServiceServer::new(ExporterServer {
        shared: shared.clone(),
    });
    let router = RouterServiceServer::new(crate::tunnel::RouterServer::new(shared));

    Ok(tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_service(exporter)
            .add_service(router)
            .serve_with_incoming(incoming)
            .await
        {
            tracing::error!(error = %e, "session server exited");
        }
    }))
}

/// The tonic `ExporterService` implementation over the [`SharedSession`].
struct ExporterServer {
    shared: Arc<SharedSession>,
}

#[tonic::async_trait]
impl ExporterService for ExporterServer {
    async fn get_report(&self, _req: Request<()>) -> Result<Response<GetReportResponse>, Status> {
        // The cached full-tree report of the current lease (empty when idle).
        Ok(Response::new(
            self.shared
                .routing()
                .map(|r| r.report.clone())
                .unwrap_or_default(),
        ))
    }

    async fn driver_call(
        &self,
        req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        let req = req.into_inner();
        // Route by UUID, then forward the typed response / tonic Status unchanged —
        // the host owns marker lookup, Value marshaling, and exception mapping.
        self.shared
            .require_routing()?
            .route(&req.uuid)?
            .driver_call(req)
            .await
    }

    type StreamingDriverCallStream = Streaming<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        let req = req.into_inner();
        let stream = self
            .shared
            .require_routing()?
            .route(&req.uuid)?
            .streaming_driver_call(req)
            .await?
            .into_inner();
        Ok(Response::new(stream))
    }

    type LogStreamStream = Pin<Box<dyn Stream<Item = Result<LogStreamResponse, Status>> + Send>>;
    async fn log_stream(
        &self,
        _req: Request<()>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        // inc3: proxy the current lease's host LogStream; nothing when idle (inc-later
        // aggregates Rust-side with hook-output phase tagging, design §4.1).
        match self.shared.routing() {
            Some(routing) => {
                let stream = routing.host_client().log_stream(()).await?.into_inner();
                Ok(Response::new(Box::pin(stream)))
            }
            None => Ok(Response::new(Box::pin(tokio_stream::empty()))),
        }
    }

    async fn reset(&self, _req: Request<ResetRequest>) -> Result<Response<ResetResponse>, Status> {
        // Frozen quirk: Reset is UNIMPLEMENTED (jumpstarter.proto:142), a feature probe.
        Err(Status::unimplemented("Reset is not implemented"))
    }

    async fn get_status(
        &self,
        _req: Request<GetStatusRequest>,
    ) -> Result<Response<GetStatusResponse>, Status> {
        // Answered from the lease FSM snapshot, so it works across leases (the host is
        // per-lease and stays at LEASE_READY, so it cannot report the real lifecycle).
        Ok(Response::new(self.shared.status.borrow().to_response()))
    }

    async fn end_session(
        &self,
        _req: Request<EndSessionRequest>,
    ) -> Result<Response<EndSessionResponse>, Status> {
        // Signal the active lease task to run afterLease early, then return at once
        // (session.py:381-420). Idle => no lease to end.
        let signal = self.shared.end_session.borrow().clone();
        tracing::debug!(active = signal.is_some(), "EndSession RPC received");
        match signal {
            Some(signal) => {
                signal.notify_one();
                Ok(Response::new(EndSessionResponse {
                    success: true,
                    message: Some(
                        "Session end triggered, afterLease hook running asynchronously".to_string(),
                    ),
                }))
            }
            None => Ok(Response::new(EndSessionResponse {
                success: false,
                message: Some("No active lease context".to_string()),
            })),
        }
    }
}
