//! Controller-RPC plumbing shared by the exporter loop and the hook orchestration.

use hyper_util::rt::TokioIo;
use jumpstarter_client::AuthInterceptor;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::{ExporterStatus, ReportStatusRequest};
use tokio::net::UnixStream;
use tonic::service::interceptor::InterceptedService;
use tonic::transport::{Channel, Endpoint};

use crate::Error;

/// The authenticated `ControllerService` client (role `"Exporter"`). Cheaply
/// cloneable — clones share the underlying channel.
pub type Controller = ControllerServiceClient<InterceptedService<Channel, AuthInterceptor>>;

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
