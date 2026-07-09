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
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;

use jumpstarter_driver_core::legacy::LegacyDispatch;
use jumpstarter_protocol::v1::exporter_service_server::{ExporterService, ExporterServiceServer};
use jumpstarter_protocol::v1::router_service_server::RouterServiceServer;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, EndSessionRequest, EndSessionResponse,
    GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse, ResetRequest,
    ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use jumpstarter_transport::demux::{Demux, Router as DemuxRouter};
use tokio::net::UnixListener;
use tokio::sync::{watch, Notify};
use tokio::task::JoinHandle;
use tokio_stream::wrappers::UnixListenerStream;
use tokio_stream::{Stream, StreamExt as _};
use tonic::service::Routes;
use tonic::transport::Server;
use tonic::{Request, Response, Status};

use crate::backend::{DriverBackend, ResponseStream};
use crate::control::StatusSnapshot;
use crate::Error;

/// tonic's default per-message size limit (4 MiB) — used for the standalone hook socket, which
/// (unlike the bulk client/TCP path) carries no large resource/flash frames and kept tonic's
/// default before the demux wiring; named so `session_routes` preserves that exactly.
const DEFAULT_MAX_MESSAGE_SIZE: usize = 4 * 1024 * 1024;

/// A single lease's driver routing: the slim host channel, its cached full-tree
/// `GetReport`, and the set of valid driver UUIDs (all routing to the one host; a
/// single host owns the whole tree, so Proxy duplicates collapse like
/// `Session.mapping`).
pub struct RoutingTable {
    backend: Arc<dyn DriverBackend>,
    driver_uuids: HashSet<String>,
    report: GetReportResponse,
    /// The server-side legacy `DriverCall` compatibility shim, built once from this lease's driver
    /// descriptors — translates an old client's `DriverCall` into the native dispatch.
    legacy: Arc<LegacyDispatch>,
}

impl RoutingTable {
    /// Build from the host's full-tree `GetReport` (cached for the lease — UUIDs are
    /// frozen for the host's lifetime, `metadata.py:7-10`). The backend is the lease's
    /// driver host: the slim subprocess today, an in-process foreign host later.
    pub async fn build(backend: Arc<dyn DriverBackend>) -> Result<Self, Error> {
        let report = backend.get_report().await?;
        let driver_uuids = report.reports.iter().map(|r| r.uuid.clone()).collect();
        let legacy = Arc::new(LegacyDispatch::from_reports(&report.reports));
        Ok(Self {
            backend,
            driver_uuids,
            report,
            legacy,
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

    /// The legacy `DriverCall` compatibility shim for this lease.
    pub(crate) fn legacy(&self) -> Arc<LegacyDispatch> {
        self.legacy.clone()
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
}

/// Routes a native per-driver gRPC call (`/jumpstarter.driver.*.v1.*`) to its backend by the
/// `x-jumpstarter-driver-uuid` header, for the [`Demux`] catch-all fallback.
///
/// This is the native-gRPC counterpart of [`ExporterServer::driver_call`]'s typed routing: both
/// resolve a uuid against the current lease's [`RoutingTable`]. Resolution mirrors `route()` —
/// `None` when idle (no lease) or the uuid is unknown — and the demux turns that `None` into a
/// `NOT_FOUND` at the wire boundary, so an unknown/idle native call is rejected rather than 404ing.
/// Readiness gating (`require_ready`) is left to the demux/host: an opaque native call carries no
/// typed phase contract, and a not-yet-ready host simply rejects it.
struct SessionRouter(Arc<SharedSession>);

impl DemuxRouter for SessionRouter {
    fn backend(&self, uuid: &str) -> Option<Arc<dyn DriverBackend>> {
        self.0.routing().and_then(|rt| rt.route(uuid).ok())
    }
}

/// Build the combined service tree for a session socket: the typed [`ExporterService`] +
/// [`RouterService`] as named gRPC routes, with the native [`Demux`] mounted as the **fallback**
/// so any unknown method path (a native `jumpstarter.driver.*.v1.*` call) routes to the
/// per-driver backend instead of returning tonic's default `UNIMPLEMENTED`.
///
/// Returns a [`Routes`] (re-wrapped from the combined `axum::Router`) so callers keep using
/// `Server::builder()`'s h2 tuning + `add_routes(..).serve_with_incoming(..)`. The typed services
/// are added via tonic's `Routes` (which registers them at `/{service}/*rest`); we then swap that
/// router's default fallback for the demux. `route_max_*` carries the large message limits onto
/// the typed `RouterService` (bulk resource/flash frames) exactly as before.
fn session_routes(shared: Arc<SharedSession>, route_max_message_size: usize) -> Routes {
    let exporter = ExporterServiceServer::new(ExporterServer {
        shared: shared.clone(),
    });
    let router = RouterServiceServer::new(crate::tunnel::RouterServer::new(shared.clone()))
        .max_decoding_message_size(route_max_message_size)
        .max_encoding_message_size(route_max_message_size);

    // Build the typed routes the normal way, then drop down to the underlying `axum::Router` to
    // override its fallback (tonic's default is the `UNIMPLEMENTED` handler) with the native demux.
    // `Demux<R>` itself is the `tower::Service` axum's `fallback_service` wants, so we mount it
    // directly (no extra accessor on the transport crate needed). `Routes: From<axum::Router>` lets
    // us re-wrap it so `Server::builder().add_routes(..)` still applies the h2 tuning.
    let mut builder = Routes::builder();
    builder.add_service(exporter).add_service(router);
    let axum_router = builder
        .routes()
        .into_axum_router()
        .fallback_service(Demux::new(SessionRouter(shared)));
    Routes::from(axum_router)
}

/// Serve a single native driver `backend` on `uds` for the polyglot hub, until the process is
/// killed. This is the host-SDK entrypoint for a **standalone, pre-compiled native driver host**:
/// a driver crate builds its own `jumpstarter-driver-<crate>-host` binary that wraps its driver in
/// a [`DriverBackend`], then calls this — depending only on the host SDK (`jumpstarter-driver`,
/// `-exporter`, `-config`), never the `jmp` CLI. The hub dials `uds` and federates the entry.
///
/// Mirrors the foreign-host path (`jumpstarter_core_uniffi::serve_driver_host`): build the routing
/// table, pin the session watch channels (no lease loop — one fixed tree for the host's lifetime),
/// and serve the driver-host seam. The caller installs the parent-death watchdog
/// ([`crate::exit_when_orphaned`]) before awaiting this.
pub async fn serve_native_host(uds: &Path, backend: Arc<dyn DriverBackend>) -> Result<(), Error> {
    let routing = RoutingTable::build(backend).await?;
    // Pin the session watch channels — there is no lease loop here (one fixed driver tree for the
    // host's lifetime); the senders are held so receivers never observe `Closed`.
    let (_rtx, routing_rx) = watch::channel(Some(Arc::new(routing)));
    let (_stx, status_rx) = watch::channel(StatusSnapshot::default());
    let (_etx, end_rx) = watch::channel(None);
    let shared = SharedSession::new(routing_rx, status_rx, end_rx, crate::logbuf::HookLog::new());

    // The hook socket is `<uds>.hook` (append, not replace-extension — `uds` ends in `.sock`).
    let mut hook = uds.as_os_str().to_owned();
    hook.push(".hook");
    let hook_path = PathBuf::from(hook);

    let server = serve(shared, uds, &hook_path)?;
    // Serve until the hub SIGKILLs us at lease end (or a signal terminates the process).
    let _ = server.await;
    Ok(())
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
    // Typed ExporterService/RouterService + the native per-driver demux as the catch-all fallback.
    let routes = session_routes(shared, 64 * 1024 * 1024);

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
            .add_routes(routes)
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

    // Hook socket (internal): unauthenticated, like the controller-mode hook socket. Typed
    // services + the native demux fallback (the hook `j` may issue native per-driver calls).
    // Keep the default message limits the hook socket used before (no large-frame bulk path here).
    let hook = UnixListener::bind(hook_path)
        .map_err(|e| Error::Config(format!("binding hook session socket: {e}")))?;
    let hook_routes = session_routes(shared.clone(), DEFAULT_MAX_MESSAGE_SIZE);
    let hook_task = tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_routes(hook_routes)
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
    // Typed services + the native demux fallback, with the passphrase interceptor applied as a
    // server-wide `Layer` (rather than per-typed-service `with_interceptor`). Layering wraps the
    // whole `Routes` — typed routes *and* the native demux fallback — so native per-driver calls
    // are passphrase-gated identically; the large message limits are set on the typed
    // `RouterService` inside `session_routes` (before the layer), matching the prior behavior.
    let interceptor =
        tonic::service::interceptor::interceptor(crate::auth::passphrase_interceptor(passphrase));
    let routes = session_routes(shared, 64 * 1024 * 1024);
    let tcp_task = tokio::spawn(async move {
        if let Err(e) = Server::builder()
            // Large h2 windows + frame size + nodelay for bulk resource/flash throughput
            // (see the controller-mode server above).
            .initial_stream_window_size(8 * 1024 * 1024)
            .initial_connection_window_size(16 * 1024 * 1024)
            .max_frame_size(1024 * 1024)
            .tcp_nodelay(true)
            .layer(interceptor)
            .add_routes(routes)
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

    // --- legacy backwards-compat shim (old clients only) ---------------------------------------
    // New clients invoke drivers via the native per-interface demux (the `Demux` fallback). The two
    // handlers below serve OLD clients' generic `DriverCall`/`StreamingDriverCall` by translating
    // them into that same native dispatch (`LegacyDispatch`), so there is no separate legacy path in
    // the backends. A missing lease is `UNKNOWN`; the native dispatch underneath does the rest.

    async fn driver_call(
        &self,
        req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        let req = req.into_inner();
        tracing::debug!(uuid = %req.uuid, method = %req.method, "DriverCall (legacy shim)");
        let routing = self
            .shared
            .routing()
            .ok_or_else(|| Status::unknown("no active lease"))?;
        let resp = routing
            .legacy()
            .driver_call(&*routing.backend(), req)
            .await?;
        Ok(Response::new(resp))
    }

    type StreamingDriverCallStream = ResponseStream<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        let req = req.into_inner();
        tracing::debug!(uuid = %req.uuid, method = %req.method, "StreamingDriverCall (legacy shim)");
        let routing = self
            .shared
            .routing()
            .ok_or_else(|| Status::unknown("no active lease"))?;
        let stream = routing
            .legacy()
            .streaming_driver_call(routing.backend(), req)
            .await?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::ResponseStream;
    use bytes::Bytes;
    use jumpstarter_protocol::v1::{DriverInstanceReport, GetReportResponse};
    use jumpstarter_transport::demux::{BytesCodec, DRIVER_UUID_KEY};
    use jumpstarter_transport::transport::{connect_channel, InProcessTransport, Transport};
    use std::str::FromStr;
    use tonic::metadata::MetadataMap;

    const DRIVER_UUID: &str = "driver-under-test";
    const NATIVE_PATH: &str = "/jumpstarter.driver.power.v1.PowerInterface/Echo";

    /// A stub driver host: reports a single driver (so the [`RoutingTable`] knows its uuid) and
    /// echoes any opaque native unary call back through `forward_unary`. Every other backend method
    /// is unreachable — the native path must not touch them.
    struct EchoBackend;

    #[tonic::async_trait]
    impl DriverBackend for EchoBackend {
        async fn get_report(&self) -> Result<GetReportResponse, Status> {
            Ok(GetReportResponse {
                reports: vec![DriverInstanceReport {
                    uuid: DRIVER_UUID.to_string(),
                    ..Default::default()
                }],
                ..Default::default()
            })
        }
        async fn open_router_stream(
            &self,
            _request_meta: tonic::metadata::AsciiMetadataValue,
            _uplink: crate::backend::FrameUplink,
        ) -> Result<crate::backend::RouterStreamOpen, Status> {
            unreachable!()
        }
        async fn log_stream(
            &self,
        ) -> Result<ResponseStream<jumpstarter_protocol::v1::LogStreamResponse>, Status> {
            unreachable!()
        }
        async fn forward_unary(
            &self,
            path: &str,
            _metadata: MetadataMap,
            body: Bytes,
        ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {
            // The opaque body is echoed verbatim, and the method path is carried back in the
            // initial metadata (which the demux propagates) so the test can confirm the demux
            // forwarded the *exact* path it received.
            let mut initial = MetadataMap::new();
            initial.insert("x-echoed-path", path.parse().unwrap());
            Ok((initial, body, MetadataMap::new()))
        }
    }

    /// A `SharedSession` whose routing points at `backend` and whose FSM reads `LEASE_READY`.
    /// The `watch::Sender`s are leaked into a returned `Box` so the receivers stay live for the
    /// session's lifetime.
    async fn ready_session(backend: Arc<dyn DriverBackend>) -> Arc<SharedSession> {
        let routing = RoutingTable::build(backend).await.expect("build routing");
        let (routing_tx, routing_rx) = watch::channel(Some(Arc::new(routing)));
        let snapshot = StatusSnapshot {
            status: jumpstarter_protocol::v1::ExporterStatus::LeaseReady,
            ..StatusSnapshot::default()
        };
        let (status_tx, status_rx) = watch::channel(snapshot);
        let (end_tx, end_rx) = watch::channel(None);
        // Keep the senders alive for the test's duration.
        Box::leak(Box::new((routing_tx, status_tx, end_tx)));
        SharedSession::new(routing_rx, status_rx, end_rx, crate::logbuf::HookLog::new())
    }

    /// A native per-driver method path (`/jumpstarter.driver.*.v1.*`) — unknown to the typed
    /// `ExporterService`/`RouterService` — is served by the demux fallback, routed by the
    /// `x-jumpstarter-driver-uuid` header to the lease's backend, and echoed back. This proves the
    /// native fallback is genuinely wired into the live session server (no `UNIMPLEMENTED`/404).
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn native_unary_routes_through_session_router() {
        let shared = ready_session(Arc::new(EchoBackend)).await;
        let routes = session_routes(shared, 64 * 1024 * 1024);

        let transport = InProcessTransport::new();
        let incoming = transport.incoming();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_routes(routes)
                .serve_with_incoming(incoming)
                .await
        });

        let channel = connect_channel(&transport).await.expect("dial session");
        let mut client = tonic::client::Grpc::new(channel);
        client.ready().await.expect("client ready");

        let payload = Bytes::from_static(b"\x08\x01opaque-native-proto");
        let mut request = Request::new(payload.clone());
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, DRIVER_UUID.parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(NATIVE_PATH).unwrap();
        let response = client
            .unary(request, path, BytesCodec)
            .await
            .expect("native unary forwarded through session demux");

        // The forwarded path is echoed in a trailer, and the opaque body round-trips unchanged.
        assert_eq!(
            response
                .metadata()
                .get("x-echoed-path")
                .and_then(|v| v.to_str().ok()),
            Some(NATIVE_PATH)
        );
        assert_eq!(response.into_inner(), payload);

        server.abort();
    }

    /// A native call for a uuid the lease does not own is rejected (the demux turns the router's
    /// `None` into `NOT_FOUND`) rather than reaching a backend — confirming header-keyed routing.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn native_unary_unknown_uuid_rejected() {
        let shared = ready_session(Arc::new(EchoBackend)).await;
        let routes = session_routes(shared, 64 * 1024 * 1024);

        let transport = InProcessTransport::new();
        let incoming = transport.incoming();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_routes(routes)
                .serve_with_incoming(incoming)
                .await
        });

        let channel = connect_channel(&transport).await.expect("dial session");
        let mut client = tonic::client::Grpc::new(channel);
        client.ready().await.expect("client ready");

        let mut request = Request::new(Bytes::new());
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, "no-such-driver".parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(NATIVE_PATH).unwrap();
        let err = client
            .unary(request, path, BytesCodec)
            .await
            .expect_err("unknown driver uuid must be rejected");
        assert_eq!(err.code(), tonic::Code::NotFound);

        server.abort();
    }
}
