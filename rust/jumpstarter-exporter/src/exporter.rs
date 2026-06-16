//! The exporter runtime loop (spec doc 03; increment 1).
//!
//! Registers with the controller, consumes the server-streaming `Status` RPC
//! (push lease notifications), and serves one lease at a time: on a lease, open
//! `Listen` and bridge each `ListenResponse` from the session socket to the router
//! (the reverse of the Phase-A client transport). On shutdown, report `OFFLINE`
//! and `Unregister`.
//!
//! Deferred to later increments: hooks, the supervisor fork/restart loop, the full
//! lease-lifecycle FSM, the `_retry_stream` contract (5×1.0 s with the transient
//! fast-path), standalone TCP, and per-lease driver re-instantiation.

use std::path::PathBuf;
use std::time::Duration;

use hyper_util::rt::TokioIo;
use jumpstarter_client::channel;
use jumpstarter_client::router;
use jumpstarter_client::AuthInterceptor;
use jumpstarter_config::ExporterConfig;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::{
    ExporterStatus, ListenRequest, RegisterRequest, ReportStatusRequest, StatusRequest,
    UnregisterRequest,
};
use tokio::net::UnixStream;
use tokio::task::JoinHandle;
use tokio::time::sleep;
use tokio_stream::StreamExt as _;
use tonic::service::interceptor::InterceptedService;
use tonic::transport::{Channel, Endpoint};

use crate::driver_host::DriverHost;
use crate::Error;

type Controller = ControllerServiceClient<InterceptedService<Channel, AuthInterceptor>>;

/// Options for [`run`].
pub struct RunOptions {
    pub config: ExporterConfig,
    /// Path of the exporter config file (passed to the Python driver host).
    pub config_path: PathBuf,
}

/// Run the exporter until a shutdown signal (SIGINT/SIGTERM).
pub async fn run(opts: RunOptions) -> Result<(), Error> {
    let config = &opts.config;
    let endpoint = config
        .endpoint
        .clone()
        .ok_or_else(|| Error::Config("exporter endpoint not set in config".into()))?;
    let token = config
        .token
        .clone()
        .ok_or_else(|| Error::Config("exporter token not set in config".into()))?;
    let namespace = config.metadata.namespace.clone().unwrap_or_default();
    let name = config.metadata.name.clone();

    // 1. Host the drivers in a Python subprocess; learn the session socket.
    let host = DriverHost::spawn(&opts.config_path).await?;

    // 2. Authenticated controller channel (role "Exporter").
    let svc = channel::connect_controller(
        &endpoint,
        &config.tls,
        "Exporter",
        &token,
        &namespace,
        &name,
    )
    .await?;
    let mut controller = ControllerServiceClient::new(svc);

    // 3. Register: GetReport on the session socket -> Register -> AVAILABLE.
    let report = ExporterServiceClient::new(uds_channel(host.socket()).await?)
        .get_report(())
        .await?
        .into_inner();
    // RegisterResponse is intentionally discarded — identity stays config-derived
    // (exporter.py:324-329).
    controller
        .register(RegisterRequest {
            labels: report.labels,
            reports: report.reports,
        })
        .await?;
    report_status(
        &mut controller,
        ExporterStatus::Available,
        "Exporter registered and available",
    )
    .await?;
    tracing::info!(%name, "exporter registered");

    // 4. Consume Status (lease transitions) until a shutdown signal.
    let outcome = tokio::select! {
        r = status_loop(controller.clone(), &host, config) => r,
        _ = shutdown_signal() => {
            tracing::info!("shutdown signal received");
            Ok(())
        }
    };

    // 5. Best-effort unregister.
    let _ = report_status(
        &mut controller,
        ExporterStatus::Offline,
        "Exporter shutting down",
    )
    .await;
    let _ = controller
        .unregister(UnregisterRequest {
            reason: "Exporter shutdown".to_string(),
        })
        .await;
    tracing::info!("exporter unregistered");
    outcome
}

/// Consume the Status stream, starting/ending a single lease as transitions
/// arrive. Reconnects on stream close (the stream doubles as the liveness signal).
async fn status_loop(
    mut controller: Controller,
    host: &DriverHost,
    config: &ExporterConfig,
) -> Result<(), Error> {
    let mut lease_task: Option<JoinHandle<()>> = None;
    let mut lease_name: Option<String> = None;

    loop {
        let mut stream = match controller.status(StatusRequest {}).await {
            Ok(r) => r.into_inner(),
            Err(e) => {
                tracing::warn!(error = %e, "opening Status stream failed; retrying in 1s");
                sleep(Duration::from_secs(1)).await;
                continue;
            }
        };

        while let Some(item) = stream.next().await {
            let resp = match item {
                Ok(r) => r,
                Err(e) => {
                    tracing::warn!(error = %e, "Status stream error");
                    break;
                }
            };
            let leased = resp.leased && resp.lease_name.as_deref().is_some_and(|s| !s.is_empty());

            if leased && lease_name.is_none() {
                let name = resp.lease_name.clone().unwrap();
                tracing::info!(lease = %name, "lease started");
                lease_task = Some(spawn_lease(
                    controller.clone(),
                    name.clone(),
                    host.socket().to_string(),
                    config.tls.clone(),
                ));
                lease_name = Some(name);
            } else if !leased && lease_name.is_some() {
                tracing::info!("lease ended");
                if let Some(h) = lease_task.take() {
                    h.abort();
                }
                lease_name = None;
                let _ = report_status(
                    &mut controller,
                    ExporterStatus::Available,
                    "Available for new lease",
                )
                .await;
                // Inter-lease settle, avoids overlapping-session SSL corruption
                // (exporter.py:853-855).
                sleep(Duration::from_millis(200)).await;
            }
        }

        tracing::debug!("Status stream closed; reconnecting");
        sleep(Duration::from_secs(1)).await;
    }
}

/// Per-lease task: open `Listen`, report `LEASE_READY`, and bridge each incoming
/// `ListenResponse` from the session socket to the router.
fn spawn_lease(
    mut controller: Controller,
    lease_name: String,
    socket: String,
    tls: jumpstarter_config::TlsConfig,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        // Report LEASE_READY *before* opening Listen. The controller only permits a
        // client Dial once the exporter is LEASE_READY, and it sends Listen stream
        // headers only when it has a ListenResponse to deliver (i.e. after a Dial),
        // so opening Listen first would deadlock. Any Dial that races ahead is held
        // in the controller's per-lease Listen queue until we connect.
        // (No hooks in increment 1 — exporter.py:756-761.)
        if let Err(e) = report_status(
            &mut controller,
            ExporterStatus::LeaseReady,
            "Ready for commands",
        )
        .await
        {
            tracing::error!(error = %e, "reporting LEASE_READY failed");
            return;
        }

        let mut listen = match controller
            .listen(ListenRequest {
                lease_name: lease_name.clone(),
            })
            .await
        {
            Ok(r) => r.into_inner(),
            Err(e) => {
                tracing::error!(error = %e, "opening Listen stream failed");
                return;
            }
        };
        tracing::info!(lease = %lease_name, "LEASE_READY; awaiting connection requests");

        while let Some(item) = listen.next().await {
            let resp = match item {
                Ok(r) => r,
                Err(e) => {
                    tracing::warn!(error = %e, "Listen stream error");
                    break;
                }
            };
            tracing::info!(router = %resp.router_endpoint, "handling connection request");
            let socket = socket.clone();
            let tls = tls.clone();
            // One bridge per incoming client connection (multiple concurrent
            // connections per lease are allowed).
            tokio::spawn(async move {
                match UnixStream::connect(&socket).await {
                    Ok(stream) => {
                        if let Err(e) =
                            router::bridge(stream, &resp.router_endpoint, &resp.router_token, &tls)
                                .await
                        {
                            tracing::warn!(error = %e, "router bridge failed");
                        }
                    }
                    Err(e) => tracing::warn!(error = %e, "connecting to session socket failed"),
                }
            });
        }
    })
}

async fn report_status(
    controller: &mut Controller,
    status: ExporterStatus,
    message: &str,
) -> Result<(), Error> {
    let req = ReportStatusRequest {
        status: status as i32,
        message: Some(message.to_string()),
        release_lease: None,
    };
    match controller.report_status(req).await {
        Ok(_) => Ok(()),
        // Old controllers lack ReportStatus — treat UNIMPLEMENTED as a feature probe.
        Err(s) if s.code() == tonic::Code::Unimplemented => Ok(()),
        Err(s) => Err(s.into()),
    }
}

/// Build a tonic channel over a local Unix socket (the session's `ExporterService`).
async fn uds_channel(path: &str) -> Result<Channel, Error> {
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

async fn shutdown_signal() {
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigint = match signal(SignalKind::interrupt()) {
        Ok(s) => s,
        Err(_) => return std::future::pending().await,
    };
    let mut sigterm = match signal(SignalKind::terminate()) {
        Ok(s) => s,
        Err(_) => return std::future::pending().await,
    };
    tokio::select! {
        _ = sigint.recv() => {}
        _ = sigterm.recv() => {}
    }
}
