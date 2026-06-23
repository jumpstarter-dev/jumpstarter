//! Controller-RPC plumbing shared by the exporter loop and the hook orchestration.

use std::sync::Arc;

use hyper_util::rt::TokioIo;
use jumpstarter_client::AuthInterceptor;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::{ExporterStatus, GetStatusResponse, ReportStatusRequest};
use tokio::net::UnixStream;
use tokio::sync::watch;
use tonic::service::interceptor::InterceptedService;
use tonic::transport::{Channel, Endpoint};

use crate::Error;

/// Latches the first time the controller answers `ReportStatus` with `UNIMPLEMENTED`, so the
/// "controller does not support ReportStatus" warning is logged once per process instead of on
/// every status transition (`exporter.py:360-366` warned here; status updates are then skipped).
static REPORT_UNIMPLEMENTED_WARNED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Warn once that the controller rejected `ReportStatus` as `UNIMPLEMENTED` (subsequent calls
/// are silent), so the log isn't spammed on every transition against an old controller.
fn warn_report_unimplemented_once(status: ExporterStatus) {
    if !REPORT_UNIMPLEMENTED_WARNED.swap(true, std::sync::atomic::Ordering::Relaxed) {
        tracing::warn!(
            ?status,
            "ReportStatus unsupported by controller; status updates will be skipped"
        );
    }
}

/// The authenticated `ControllerService` client (role `"Exporter"`). Cheaply
/// cloneable — clones share the underlying channel.
pub type Controller = ControllerServiceClient<InterceptedService<Channel, AuthInterceptor>>;

/// The exporter's reported status, projected for the `GetStatus` RPC (spec doc 03;
/// `session.py:339-375`). `version` is monotonic — bumped on every report, not just
/// transitions — so a client polling `GetStatus` never misses a transition.
#[derive(Debug, Clone)]
pub struct StatusSnapshot {
    pub status: ExporterStatus,
    pub message: String,
    pub version: u64,
    pub previous: Option<ExporterStatus>,
}

impl Default for StatusSnapshot {
    fn default() -> Self {
        // Match Python's initial AVAILABLE (session.py) for the brief pre-registration
        // window, so a `GetStatus` before the first report never reads UNSPECIFIED.
        Self {
            status: ExporterStatus::Available,
            message: String::new(),
            version: 0,
            previous: None,
        }
    }
}

impl StatusSnapshot {
    pub fn to_response(&self) -> GetStatusResponse {
        GetStatusResponse {
            status: self.status as i32,
            message: Some(self.message.clone()),
            status_version: self.version,
            previous_status: self.previous.map(|p| p as i32),
        }
    }

    fn apply(&mut self, status: ExporterStatus, message: &str) {
        self.previous = (self.status != status).then_some(self.status);
        self.status = status;
        message.clone_into(&mut self.message);
        self.version += 1;
    }
}

/// Whether the controller accepted a reported status transition (DD-7). The lease runner
/// must not advance into a state whose status report was `Rejected`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReportOutcome {
    Accepted,
    Rejected(String),
}

/// The single status choke point: reports a status transition to the controller AND
/// mirrors it into the local [`StatusSnapshot`] (so `GetStatus` answers from the lease
/// FSM, across leases, without round-tripping the per-lease host). Cheaply cloneable — clones
/// share the controller channel and the snapshot sender, so the runner and a spawned hook
/// effect can both report.
#[derive(Clone)]
pub struct StatusReporter {
    /// `None` in standalone (`--tls-grpc-listener`) mode — there is no controller, so
    /// status is only mirrored into the local snapshot for `GetStatus`/hooks.
    controller: Option<Controller>,
    snapshot: Arc<watch::Sender<StatusSnapshot>>,
}

impl StatusReporter {
    pub fn new(controller: Controller, snapshot: Arc<watch::Sender<StatusSnapshot>>) -> Self {
        Self {
            controller: Some(controller),
            snapshot,
        }
    }

    /// A controller-free reporter for standalone serving: hooks still drive the
    /// `StatusSnapshot` (so `GetStatus` reflects the lifecycle), but nothing is sent
    /// to a controller.
    pub fn standalone(snapshot: Arc<watch::Sender<StatusSnapshot>>) -> Self {
        Self {
            controller: None,
            snapshot,
        }
    }

    /// The underlying controller client (for `Register`/`Unregister`/`Status`).
    /// Only valid in controller mode.
    pub fn controller(&mut self) -> &mut Controller {
        self.controller
            .as_mut()
            .expect("controller() called in standalone mode")
    }

    /// Report a status transition (best-effort to the controller when present; always
    /// recorded locally for `GetStatus`).
    pub async fn report(&mut self, status: ExporterStatus, message: &str) {
        if let Some(controller) = &mut self.controller {
            if let Err(e) = report_status(controller, status, message).await {
                tracing::warn!(error = %e, ?status, "failed to report status to controller");
            }
        }
        self.snapshot.send_modify(|s| s.apply(status, message));
    }

    /// Report a status transition and surface whether the controller accepted it (DD-7). On
    /// an explicit `FAILED_PRECONDITION`/`ABORTED` the transition is `Rejected` and the local
    /// snapshot is left unchanged (the FSM must not advance). `UNIMPLEMENTED` (feature probe)
    /// and transient transport errors are treated as `Accepted` so a blip never tears down a
    /// healthy lease — matching the resilience of the best-effort [`Self::report`].
    pub async fn try_report(&mut self, status: ExporterStatus, message: &str) -> ReportOutcome {
        if let Some(controller) = &mut self.controller {
            let request = ReportStatusRequest {
                status: status as i32,
                message: Some(message.to_string()),
                release_lease: None,
            };
            match controller.report_status(request).await {
                Ok(_) => {
                    tracing::debug!(?status, message, "reported status");
                }
                Err(s)
                    if matches!(
                        s.code(),
                        tonic::Code::FailedPrecondition | tonic::Code::Aborted
                    ) =>
                {
                    return ReportOutcome::Rejected(s.message().to_string());
                }
                Err(s) if s.code() == tonic::Code::Unimplemented => {
                    warn_report_unimplemented_once(status);
                }
                Err(s) => {
                    tracing::warn!(error = %s, ?status, "report_status failed (transient); proceeding");
                }
            }
        }
        self.snapshot.send_modify(|s| s.apply(status, message));
        ReportOutcome::Accepted
    }

    /// Ask the controller to release the active lease, also recording the implied
    /// `AVAILABLE` locally. A no-op against the controller in standalone mode.
    pub async fn request_release(&mut self, message: &str) {
        if let Some(controller) = &mut self.controller {
            if let Err(e) = request_release(controller, message).await {
                tracing::warn!(error = %e, "failed to request lease release");
            }
        }
        self.snapshot
            .send_modify(|s| s.apply(ExporterStatus::Available, message));
    }
}

/// Report an exporter status transition. `UNIMPLEMENTED` is treated as a feature
/// probe against old controllers (`exporter.py:360-366`).
pub async fn report_status(
    controller: &mut Controller,
    status: ExporterStatus,
    message: &str,
) -> Result<(), Error> {
    report(controller, status, message, None).await
}

/// Ask the controller to release the active lease (`release_lease=true`,
/// `exporter.py:390-399`). Used when the exporter ends a lease itself (e.g. an
/// `endLease` hook failure); the controller responds by ending the lease, which the
/// exporter then observes as `leased=false` on its `Status` stream.
pub async fn request_release(controller: &mut Controller, message: &str) -> Result<(), Error> {
    report(controller, ExporterStatus::Available, message, Some(true)).await
}

async fn report(
    controller: &mut Controller,
    status: ExporterStatus,
    message: &str,
    release_lease: Option<bool>,
) -> Result<(), Error> {
    let request = ReportStatusRequest {
        status: status as i32,
        message: Some(message.to_string()),
        release_lease,
    };
    match controller.report_status(request).await {
        Ok(_) => {
            if release_lease == Some(true) {
                tracing::debug!(message, "requested lease release");
            } else {
                tracing::debug!(?status, message, "reported status");
            }
            Ok(())
        }
        Err(s) if s.code() == tonic::Code::Unimplemented => {
            warn_report_unimplemented_once(status);
            Ok(())
        }
        Err(s) => Err(s.into()),
    }
}

/// Build a tonic channel over a local Unix socket (the session's `ExporterService`
/// or `RouterService`).
pub async fn uds_channel(path: &str) -> Result<Channel, Error> {
    let path = path.to_string();
    let connector = tower::service_fn(move |_: http::Uri| {
        let path = path.clone();
        async move { Ok::<_, std::io::Error>(TokioIo::new(UnixStream::connect(path).await?)) }
    });
    Endpoint::try_from("http://localhost")
        .map_err(|e| Error::Config(format!("uds endpoint: {e}")))?
        // Match the host server's enlarged HTTP/2 windows (see `session.rs`): this is the
        // hub→host UDS hop. The hub is the h2 *client* here, so these are its *receive*
        // windows — they gate the host→hub downlink (bulk read/dump + the router tunnel's
        // return path). The h2 default (~64 KiB) would otherwise cap that direction to a
        // few MiB/s with a WINDOW_UPDATE round-trip every 64 KiB.
        .initial_stream_window_size(8 * 1024 * 1024)
        .initial_connection_window_size(16 * 1024 * 1024)
        .connect_with_connector(connector)
        .await
        .map_err(Into::into)
}
