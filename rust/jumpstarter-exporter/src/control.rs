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

/// The single status choke point: reports a status transition to the controller AND
/// mirrors it into the local [`StatusSnapshot`] (so `GetStatus` answers from the lease
/// FSM, across leases, without round-tripping the per-lease host).
pub struct StatusReporter {
    controller: Controller,
    snapshot: Arc<watch::Sender<StatusSnapshot>>,
}

impl StatusReporter {
    pub fn new(controller: Controller, snapshot: Arc<watch::Sender<StatusSnapshot>>) -> Self {
        Self {
            controller,
            snapshot,
        }
    }

    /// The underlying controller client (for `Register`/`Unregister`/`Status`).
    pub fn controller(&mut self) -> &mut Controller {
        &mut self.controller
    }

    /// Report a status transition (best-effort to the controller; always recorded
    /// locally for `GetStatus`).
    pub async fn report(&mut self, status: ExporterStatus, message: &str) {
        if let Err(e) = report_status(&mut self.controller, status, message).await {
            tracing::warn!(error = %e, ?status, "failed to report status to controller");
        }
        self.snapshot.send_modify(|s| s.apply(status, message));
    }

    /// Ask the controller to release the active lease, also recording the implied
    /// `AVAILABLE` locally.
    pub async fn request_release(&mut self, message: &str) {
        if let Err(e) = request_release(&mut self.controller, message).await {
            tracing::warn!(error = %e, "failed to request lease release");
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
        Ok(_) => Ok(()),
        Err(s) if s.code() == tonic::Code::Unimplemented => Ok(()),
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
        .connect_with_connector(connector)
        .await
        .map_err(Into::into)
}
