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
use tonic::transport::Server;
use tonic::{Request, Response, Status};

use crate::backend::{DriverBackend, ResponseStream};
use crate::control::StatusSnapshot;
use crate::Error;

/// A single lease's driver routing: the slim host channel, its cached full-tree
/// `GetReport`, and the set of valid driver UUIDs (all routing to the one host; a
/// single host owns the whole tree, so Proxy duplicates collapse like
/// `Session.mapping`).
pub struct RoutingTable {
    backend: Arc<dyn DriverBackend>,
    driver_uuids: HashSet<String>,
    report: GetReportResponse,
}

impl RoutingTable {
    /// Build from the host's full-tree `GetReport` (cached for the lease — UUIDs are
    /// frozen for the host's lifetime, `metadata.py:7-10`). The backend is the lease's
    /// driver host: the slim subprocess today, an in-process foreign host later.
    pub async fn build(backend: Arc<dyn DriverBackend>) -> Result<Self, Error> {
        let report = backend.get_report().await?;
        let driver_uuids = report.reports.iter().map(|r| r.uuid.clone()).collect();
        Ok(Self {
            backend,
            driver_uuids,
            report,
        })
    }

    /// The cached report (used for the controller `RegisterRequest` and `GetReport`).
    pub fn report(&self) -> &GetReportResponse {
        &self.report
    }

    /// The lease's driver host backend (for the [`crate::tunnel`] RouterService proxy
    /// and `log_stream`).
    pub(crate) fn backend(&self) -> Arc<dyn DriverBackend> {
        self.backend.clone()
    }

    /// Whether `uuid` is a known driver instance in this lease.
    pub(crate) fn knows_uuid(&self, uuid: &str) -> bool {
        self.driver_uuids.contains(uuid)
    }

    /// Validate a driver UUID, returning the backend it routes to. Unknown UUID →
    /// `UNKNOWN`, matching `session.py:308` (the client distinguishes `NOT_FOUND`; §2.5).
    #[allow(clippy::result_large_err)]
    fn route(&self, uuid: &str) -> Result<Arc<dyn DriverBackend>, Status> {
        if self.driver_uuids.contains(uuid) {
            Ok(self.backend.clone())
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
    hook_log: Arc<crate::logbuf::HookLog>,
}

impl SharedSession {
    pub fn new(
        routing: watch::Receiver<Option<Arc<RoutingTable>>>,
        status: watch::Receiver<StatusSnapshot>,
        end_session: watch::Receiver<Option<Arc<Notify>>>,
        hook_log: Arc<crate::logbuf::HookLog>,
    ) -> Arc<Self> {
        Arc::new(Self {
            routing,
            status,
            end_session,
            hook_log,
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

    /// Gate driver calls on the FSM being in a phase where the board is reachable — matching the
    /// Python controller contract (`checkExporterStatusForDriverCalls`): DriverCall /
    /// StreamingDriverCall are only valid in `LEASE_READY` (client calls) or the
    /// `BEFORE_LEASE_HOOK` / `AFTER_LEASE_HOOK` phases (a hook's `j` runs against the hook socket).
    /// Any other phase returns `FAILED_PRECONDITION` rather than dispatching into a not-yet-ready
    /// or already-tearing-down host.
    #[allow(clippy::result_large_err)]
    fn require_ready(&self) -> Result<(), Status> {
        use jumpstarter_protocol::v1::ExporterStatus;
        let status = self.status.borrow().status;
        if matches!(
            status,
            ExporterStatus::LeaseReady
                | ExporterStatus::BeforeLeaseHook
                | ExporterStatus::AfterLeaseHook
        ) {
            Ok(())
        } else {
            Err(Status::failed_precondition(format!(
                "exporter not ready for driver calls (status: {status:?})"
            )))
        }
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
    let router = RouterServiceServer::new(crate::tunnel::RouterServer::new(shared))
        .max_decoding_message_size(64 * 1024 * 1024)
        .max_encoding_message_size(64 * 1024 * 1024);

    Ok(tokio::spawn(async move {
        if let Err(e) = Server::builder()
            // Match the client's enlarged HTTP/2 windows (see `uds_channel`): the server's
            // receive window gates a client→exporter bulk write (resource/flash), so the h2
            // default (~64 KiB) would otherwise cap inbound throughput to a few MiB/s.
            .initial_stream_window_size(8 * 1024 * 1024)
            .initial_connection_window_size(16 * 1024 * 1024)
            // SETTINGS_MAX_FRAME_SIZE tells the *client* the largest DATA frame it may send us;
            // the 16 KiB default means a 512 MiB resource write is ~32k frames (per-frame framing
            // CPU on the sender, independent of app chunk size). Raise it 64×.
            .max_frame_size(1024 * 1024)
            .tcp_nodelay(true)
            .add_service(exporter)
            .add_service(router)
            .serve_with_incoming(incoming)
            .await
        {
            tracing::error!(error = %e, "session server exited");
        }
    }))
}

/// Standalone (`--tls-grpc-listener`) serving: bind the client-facing
/// `ExporterService`+`RouterService` on a **TCP** address (plaintext h2c, guarded by
/// the passphrase interceptor) and the internal hook `ExporterService`+`RouterService`
/// on a Unix socket (no auth — only the local hook `j` connects there). The TCP
/// listener is bound synchronously so the port is open on return. Returns the two
/// server tasks (TCP, hook).
pub fn serve_standalone(
    shared: Arc<SharedSession>,
    bind: std::net::SocketAddr,
    hook_path: &Path,
    passphrase: Option<String>,
) -> Result<(JoinHandle<()>, JoinHandle<()>), Error> {
    use tokio_stream::wrappers::TcpListenerStream;

    // Hook socket (internal): unauthenticated, like the controller-mode hook socket.
    let hook = UnixListener::bind(hook_path)
        .map_err(|e| Error::Config(format!("binding hook session socket: {e}")))?;
    let hook_exporter = ExporterServiceServer::new(ExporterServer {
        shared: shared.clone(),
    });
    let hook_router = RouterServiceServer::new(crate::tunnel::RouterServer::new(shared.clone()));
    let hook_task = tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_service(hook_exporter)
            .add_service(hook_router)
            .serve_with_incoming(UnixListenerStream::new(hook))
            .await
        {
            tracing::error!(error = %e, "standalone hook server exited");
        }
    });

    // Client-facing TCP listener with the passphrase interceptor on every RPC.
    let std_listener = std::net::TcpListener::bind(bind)
        .map_err(|e| Error::Config(format!("binding TCP listener {bind}: {e}")))?;
    std_listener
        .set_nonblocking(true)
        .map_err(|e| Error::Config(format!("set_nonblocking: {e}")))?;
    let listener = tokio::net::TcpListener::from_std(std_listener)
        .map_err(|e| Error::Config(format!("tokio TCP listener: {e}")))?;
    let interceptor = crate::auth::passphrase_interceptor(passphrase);
    let exporter = ExporterServiceServer::with_interceptor(
        ExporterServer {
            shared: shared.clone(),
        },
        interceptor.clone(),
    );
    // Set the large message limits on the inner server *before* wrapping with the interceptor
    // (`with_interceptor` returns an `InterceptedService`, which has no `max_*` setters).
    let router = tonic::service::interceptor::InterceptedService::new(
        RouterServiceServer::new(crate::tunnel::RouterServer::new(shared))
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024),
        interceptor,
    );
    let tcp_task = tokio::spawn(async move {
        if let Err(e) = Server::builder()
            // Large h2 windows + frame size + nodelay for bulk resource/flash throughput
            // (see the controller-mode server above).
            .initial_stream_window_size(8 * 1024 * 1024)
            .initial_connection_window_size(16 * 1024 * 1024)
            .max_frame_size(1024 * 1024)
            .tcp_nodelay(true)
            .add_service(exporter)
            .add_service(router)
            .serve_with_incoming(TcpListenerStream::new(listener))
            .await
        {
            tracing::error!(error = %e, "standalone TCP server exited");
        }
    });

    Ok((tcp_task, hook_task))
}

/// The tonic `ExporterService` implementation over the [`SharedSession`].
struct ExporterServer {
    shared: Arc<SharedSession>,
}

#[tonic::async_trait]
impl ExporterService for ExporterServer {
    async fn get_report(&self, _req: Request<()>) -> Result<Response<GetReportResponse>, Status> {
        // The cached full-tree report of the current lease (empty when idle).
        let report = self
            .shared
            .routing()
            .map(|r| r.report.clone())
            .unwrap_or_default();
        tracing::debug!(drivers = report.reports.len(), "GetReport");
        Ok(Response::new(report))
    }

    async fn driver_call(
        &self,
        req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        let req = req.into_inner();
        tracing::debug!(uuid = %req.uuid, method = %req.method, "DriverCall");
        self.shared.require_ready()?;
        // Route by UUID, then forward the typed response / tonic Status unchanged —
        // the host owns marker lookup, Value marshaling, and exception mapping.
        let resp = match self
            .shared
            .require_routing()?
            .route(&req.uuid)?
            .driver_call(req)
            .await
        {
            Ok(resp) => resp,
            Err(status) => {
                tracing::debug!(error = %status, "DriverCall routed error");
                return Err(status);
            }
        };
        tracing::trace!(uuid = %resp.uuid, "DriverCall routed result");
        Ok(Response::new(resp))
    }

    type StreamingDriverCallStream = ResponseStream<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        let req = req.into_inner();
        tracing::debug!(uuid = %req.uuid, method = %req.method, "StreamingDriverCall");
        self.shared.require_ready()?;
        let stream = match self
            .shared
            .require_routing()?
            .route(&req.uuid)?
            .streaming_driver_call(req)
            .await
        {
            Ok(stream) => stream,
            Err(status) => {
                tracing::debug!(error = %status, "StreamingDriverCall routed error");
                return Err(status);
            }
        };
        tracing::trace!("StreamingDriverCall routed result");
        Ok(Response::new(stream))
    }

    type LogStreamStream = Pin<Box<dyn Stream<Item = Result<LogStreamResponse, Status>> + Send>>;
    async fn log_stream(
        &self,
        _req: Request<()>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        use tokio_stream::wrappers::BroadcastStream;

        let leased = self.shared.routing().is_some();
        tracing::debug!(leased, "LogStream client attached");

        // Hook output (beforeLease/afterLease): replay the buffer, then stream new
        // lines — so `--exporter-logs` shows hooks that ran before the client connected.
        let replay = tokio_stream::iter(self.shared.hook_log.replay().into_iter().map(Ok));
        let hooks =
            BroadcastStream::new(self.shared.hook_log.subscribe()).filter_map(|r| r.ok().map(Ok));

        // Driver/system logs from the current lease's host (empty when idle).
        let drivers: Self::LogStreamStream = match self.shared.routing() {
            Some(routing) => match routing.backend().log_stream().await {
                Ok(stream) => stream,
                Err(status) => {
                    // Previously this error was returned silently; surface it so an operator
                    // sees why driver/system logs are missing from `--exporter-logs`.
                    tracing::warn!(error = %status, "driver host log_stream failed");
                    return Err(status);
                }
            },
            None => Box::pin(tokio_stream::empty()),
        };

        Ok(Response::new(Box::pin(replay.chain(hooks.merge(drivers)))))
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
