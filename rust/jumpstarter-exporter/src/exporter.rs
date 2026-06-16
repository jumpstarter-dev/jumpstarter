//! The exporter runtime loop (spec doc 03).
//!
//! Registers with the controller, consumes the server-streaming `Status` RPC (push
//! lease notifications), and serves one lease at a time. Each lease runs through the
//! [`crate::fsm`] lifecycle, executing the `beforeLease`/`afterLease` [`crate::hooks`]
//! and bridging the router to the session's main socket (the reverse of the Phase-A
//! client transport). On shutdown — a signal, or a hook with `on_failure: exit` — it
//! reports `OFFLINE` and unregisters.
//!
//! Deferred to later increments: the supervisor fork/restart loop + rapid-failure
//! breaker, the `_retry_stream` contract (5×1.0 s), standalone TCP, and per-lease
//! driver re-instantiation.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_client::channel;
use jumpstarter_client::router;
use jumpstarter_config::{ExporterConfig, HookConfig, TlsConfig};
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::{
    ExporterStatus, ListenRequest, RegisterRequest, StatusRequest, UnregisterRequest,
};
use tokio::net::UnixStream;
use tokio::sync::Notify;
use tokio::task::JoinHandle;
use tokio::time::sleep;
use tokio_stream::StreamExt as _;

use crate::control::{self, uds_channel, Controller};
use crate::driver_host::DriverHost;
use crate::fsm::{LeaseLifecycle, LeasePhase};
use crate::hooks::{self, AfterOutcome, BeforeOutcome, HookContext};
use crate::Error;

/// Settle delay between leases, avoiding overlapping-session SSL corruption
/// (`exporter.py:853-855`).
const INTER_LEASE_SETTLE: Duration = Duration::from_millis(200);

/// Options for [`run`].
pub struct RunOptions {
    pub config: ExporterConfig,
    /// Path of the exporter config file (passed to the Python driver host).
    pub config_path: PathBuf,
}

/// Run the exporter until a shutdown signal (SIGINT/SIGTERM) or an `on_failure: exit`
/// hook.
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

    // 1. Host the drivers in a Python subprocess; learn the main + hook sockets.
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
    let report = ExporterServiceClient::new(uds_channel(host.main_socket()).await?)
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
    control::report_status(
        &mut controller,
        ExporterStatus::Available,
        "Exporter registered and available",
    )
    .await?;
    tracing::info!(%name, "exporter registered");

    // 4. Serve leases until a shutdown signal or an `on_failure: exit` hook.
    let shutdown = Arc::new(Notify::new());
    let outcome = tokio::select! {
        r = status_loop(controller.clone(), &host, config, shutdown.clone()) => r,
        _ = shutdown.notified() => {
            tracing::info!("exporter shutdown requested by hook");
            Ok(())
        }
        _ = shutdown_signal() => {
            tracing::info!("shutdown signal received");
            Ok(())
        }
    };

    // 5. Best-effort offline + unregister.
    let _ = control::report_status(
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

/// An in-flight lease: the lifecycle task plus the channel to signal its end.
struct ActiveLease {
    handle: JoinHandle<()>,
    /// Fired when the controller reports the lease has ended (`leased=false`).
    end: Arc<Notify>,
}

/// Consume the `Status` stream, running one lease at a time. The stream doubles as
/// the liveness signal, so it is reopened on close; an active lease survives a
/// reconnect.
async fn status_loop(
    mut controller: Controller,
    host: &DriverHost,
    config: &ExporterConfig,
    shutdown: Arc<Notify>,
) -> Result<(), Error> {
    let mut active: Option<ActiveLease> = None;

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

            match (leased, active.is_some()) {
                // A new lease while idle: start the lifecycle.
                (true, false) => {
                    let lease_name = resp.lease_name.clone().unwrap();
                    let client_name = resp.client_name.clone().unwrap_or_default();
                    tracing::info!(lease = %lease_name, client = %client_name, "lease started");
                    let end = Arc::new(Notify::new());
                    let handle = spawn_lease(
                        controller.clone(),
                        lease_name,
                        client_name,
                        host.main_socket().to_string(),
                        host.hook_socket().to_string(),
                        config.tls.clone(),
                        config.hooks.clone(),
                        end.clone(),
                        shutdown.clone(),
                    );
                    active = Some(ActiveLease { handle, end });
                }
                // The active lease ended: signal it and wait for cleanup
                // (afterLease) to finish before accepting the next lease.
                (false, true) => {
                    tracing::info!("lease ended");
                    let lease = active.take().unwrap();
                    lease.end.notify_one();
                    let _ = lease.handle.await;
                    sleep(INTER_LEASE_SETTLE).await;
                }
                // Steady state (still leased, or still idle): nothing to do.
                _ => {}
            }
        }

        tracing::debug!("Status stream closed; reconnecting");
        sleep(Duration::from_secs(1)).await;
    }
}

/// Spawn the per-lease lifecycle task.
#[allow(clippy::too_many_arguments)]
fn spawn_lease(
    mut controller: Controller,
    lease_name: String,
    client_name: String,
    main_socket: String,
    hook_socket: String,
    tls: TlsConfig,
    hooks: HookConfig,
    end: Arc<Notify>,
    shutdown: Arc<Notify>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let ctx = HookContext {
            hook_socket: &hook_socket,
            lease_name: &lease_name,
            client_name: &client_name,
        };
        let mut lc = LeaseLifecycle::new();
        let has_client = Arc::new(AtomicBool::new(false));

        advance(&mut lc, LeasePhase::Starting);

        // --- beforeLease -------------------------------------------------------
        let before_hook = hooks.before_lease.as_ref();
        if before_hook.is_some() {
            advance(&mut lc, LeasePhase::BeforeLease);
        }
        match hooks::run_before_lease(&mut controller, before_hook, &ctx).await {
            BeforeOutcome::Exit => {
                // beforeLease already reported BEFORE_LEASE_HOOK_FAILED + OFFLINE.
                advance(&mut lc, LeasePhase::Failed);
                shutdown.notify_one();
                return;
            }
            BeforeOutcome::Ready => {
                advance(&mut lc, LeasePhase::Ready);
                // Serve until the controller ends the lease. Report LEASE_READY (done
                // by run_before_lease) *before* opening Listen: the controller only
                // delivers Listen responses after a client Dial, which it only permits
                // once the exporter is LEASE_READY, so opening Listen first deadlocks.
                let listen = spawn_listen(
                    controller.clone(),
                    lease_name.clone(),
                    main_socket,
                    tls,
                    has_client.clone(),
                );
                end.notified().await;
                listen.abort();
                advance(&mut lc, LeasePhase::Ending);
            }
            BeforeOutcome::EndLease => {
                advance(&mut lc, LeasePhase::Ending);
                // Proactively ask the controller to release the lease; it will end the
                // lease and we observe `leased=false` -> our `end` signal.
                let _ = control::request_release(
                    &mut controller,
                    "Lease released after beforeLease hook failure",
                )
                .await;
                end.notified().await;
            }
        }

        // --- afterLease (only when a client actually used the board) -----------
        let after_hook = if has_client.load(Ordering::Relaxed) {
            hooks.after_lease.as_ref()
        } else {
            None
        };
        if after_hook.is_some() {
            advance(&mut lc, LeasePhase::AfterLease);
            match hooks::run_after_lease(&mut controller, after_hook, &ctx).await {
                AfterOutcome::Done => {
                    advance(&mut lc, LeasePhase::Releasing);
                    advance(&mut lc, LeasePhase::Done);
                }
                AfterOutcome::Exit => {
                    advance(&mut lc, LeasePhase::Failed);
                    shutdown.notify_one();
                }
            }
        } else {
            // No afterLease hook to run: report availability for the next lease.
            hooks::run_after_lease(&mut controller, None, &ctx).await;
            advance(&mut lc, LeasePhase::Done);
        }
    })
}

/// Open `Listen` and bridge each incoming `ListenResponse` from the session's main
/// socket to the router (the reverse of the Phase-A client transport). Records the
/// first connection request so the lifecycle knows the board was used.
fn spawn_listen(
    mut controller: Controller,
    lease_name: String,
    main_socket: String,
    tls: TlsConfig,
    has_client: Arc<AtomicBool>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
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
            has_client.store(true, Ordering::Relaxed);
            tracing::info!(router = %resp.router_endpoint, "handling connection request");
            let main_socket = main_socket.clone();
            let tls = tls.clone();
            // One bridge per incoming client connection (multiple concurrent
            // connections per lease are allowed).
            tokio::spawn(async move {
                match UnixStream::connect(&main_socket).await {
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

/// Advance the lifecycle, logging (but not aborting on) an invalid transition — a
/// caught transition error here means a control-flow bug in this module, not a
/// recoverable runtime condition.
fn advance(lc: &mut LeaseLifecycle, to: LeasePhase) {
    if let Err(e) = lc.transition(to) {
        tracing::error!(error = %e, "lease lifecycle bug: invalid transition");
    }
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
