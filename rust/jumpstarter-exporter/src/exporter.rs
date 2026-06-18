//! The exporter runtime loop (spec doc 03; native-migration §4).
//!
//! Registers with the controller (via a throwaway host), then consumes the
//! server-streaming `Status` RPC and serves one lease at a time. Each lease spawns a
//! **fresh** slim host (fresh drivers — `exporter.py:577-593`) during `Starting`,
//! swaps its routing into the process-lifetime session server, runs the
//! [`crate::fsm`] lifecycle + `beforeLease`/`afterLease` [`crate::hooks`], terminates
//! client tunnels into the Rust server, and kills the host at lease end. A client
//! `EndSession` ends the lease early (running afterLease). On shutdown — a signal or
//! an `on_failure: exit` hook — it reports `OFFLINE` and unregisters.
//!
//! Deferred to later increments: the supervisor fork/restart loop + rapid-failure
//! breaker, the `_retry_stream` contract (5×1.0 s), standalone TCP, and Rust-side
//! LogStream aggregation.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_client::channel;
use jumpstarter_client::router;
use jumpstarter_config::{ExporterConfig, HookConfig, TlsConfig};
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::{
    ExporterStatus, ListenRequest, RegisterRequest, StatusRequest, UnregisterRequest,
};
use tokio::net::UnixStream;
use tokio::sync::{watch, Notify};
use tokio::task::JoinHandle;
use tokio::time::sleep;
use tokio_stream::StreamExt as _;

use crate::control::{uds_channel, Controller, StatusReporter, StatusSnapshot};
use crate::driver_host::SlimHost;
use crate::fsm::{LeaseLifecycle, LeasePhase};
use crate::hooks::{self, AfterOutcome, BeforeOutcome, HookContext};
use crate::session::{self, RoutingTable, SharedSession};
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

/// Why a lease's serving phase ended.
#[derive(Debug, PartialEq, Eq)]
enum EndReason {
    /// The controller reported `leased=false`.
    Controller,
    /// The client called `EndSession`.
    EndSession,
    /// A `beforeLease` hook failed with `on_failure: endLease` (never served).
    EndLease,
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

    // 1. Authenticated controller channel (role "Exporter").
    let svc = channel::connect_controller(
        &endpoint,
        &config.tls,
        "Exporter",
        &token,
        &namespace,
        &name,
    )
    .await?;
    let controller = ControllerServiceClient::new(svc);

    // 2. Spawn the first host: use it for the registration `GetReport` AND keep it as
    //    the first pre-warmed host, so the first lease doesn't pay a cold spawn.
    //    Subsequent hosts are pre-warmed during the previous lease (see `Warm`); each
    //    lease still gets a freshly-spawned, reset host — just spawned ahead of time.
    let first_host = SlimHost::spawn(&opts.config_path).await?;
    let registration = RoutingTable::build(uds_channel(first_host.socket()).await?)
        .await?
        .report()
        .clone();

    // 3. Process-lifetime session server with per-lease swappable routing.
    let (routing_tx, routing_rx) = watch::channel(None);
    let (status_tx, status_rx) = watch::channel(StatusSnapshot::default());
    let (end_session_tx, end_session_rx) = watch::channel(None);
    let status_tx = Arc::new(status_tx);
    let hook_log = crate::logbuf::HookLog::new();
    let shared = SharedSession::new(routing_rx, status_rx, end_session_rx, hook_log.clone());

    let srv_dir = std::env::temp_dir().join(format!("jmp-exp-{}", std::process::id()));
    std::fs::create_dir_all(&srv_dir)
        .map_err(|e| Error::Config(format!("creating session socket dir: {e}")))?;
    let main_uds = srv_dir.join("m.sock");
    let hook_uds = srv_dir.join("h.sock");
    let _server = session::serve(shared, &main_uds, &hook_uds)?;
    let main_uds = main_uds.to_string_lossy().into_owned();
    let hook_uds = hook_uds.to_string_lossy().into_owned();

    // 4. Register from the throwaway report -> AVAILABLE.
    let mut reporter = StatusReporter::new(controller.clone(), status_tx.clone());
    reporter
        .controller()
        .register(RegisterRequest {
            labels: registration.labels,
            reports: registration.reports,
        })
        .await?;
    reporter
        .report(
            ExporterStatus::Available,
            "Exporter registered and available",
        )
        .await;
    tracing::info!(%name, "exporter registered");

    // 5. Serve leases until a shutdown signal or an `on_failure: exit` hook.
    let shutdown = Arc::new(Notify::new());
    let outcome = tokio::select! {
        r = status_loop(
            controller.clone(),
            status_tx,
            routing_tx,
            end_session_tx,
            &main_uds,
            &hook_uds,
            first_host,
            &opts.config_path,
            config,
            hook_log,
            shutdown.clone(),
        ) => r,
        _ = shutdown.notified() => {
            tracing::info!("exporter shutdown requested by hook");
            Ok(())
        }
        _ = shutdown_signal() => {
            tracing::info!("shutdown signal received");
            Ok(())
        }
    };

    // 6. Best-effort offline + unregister.
    reporter
        .report(ExporterStatus::Offline, "Exporter shutting down")
        .await;
    let _ = reporter
        .controller()
        .unregister(UnregisterRequest {
            reason: "Exporter shutdown".to_string(),
        })
        .await;
    tracing::info!("exporter unregistered");
    let _ = std::fs::remove_dir_all(&srv_dir);
    outcome
}

/// An in-flight lease: the lifecycle task, the controller-end signal, and the slim
/// host that serves it (killed when the lease ends, after the task completes).
struct ActiveLease {
    handle: JoinHandle<()>,
    /// Fired when the controller reports the lease has ended (`leased=false`).
    end: Arc<Notify>,
    host: SlimHost,
}

/// A pre-warmed slim host for the *next* lease: either ready, or still spawning in
/// the background. Pipelining the spawn (started during the previous lease) hides the
/// cold-start latency (interpreter + driver imports) so a lease rarely pays for it.
enum Warm {
    Ready(SlimHost),
    Spawning(JoinHandle<Result<SlimHost, Error>>),
}

impl Warm {
    /// Start spawning a fresh host in the background.
    fn spawn(config_path: PathBuf) -> Self {
        Warm::Spawning(tokio::spawn(
            async move { SlimHost::spawn(&config_path).await },
        ))
    }

    /// Take the warmed host (awaiting the in-flight spawn if it hasn't finished yet)
    /// and immediately kick off warming the *next* one.
    async fn take(&mut self, config_path: &Path) -> Result<SlimHost, Error> {
        match std::mem::replace(self, Warm::spawn(config_path.to_path_buf())) {
            Warm::Ready(host) => Ok(host),
            Warm::Spawning(handle) => handle
                .await
                .map_err(|e| Error::Config(format!("pre-warm host task failed: {e}")))?,
        }
    }
}

/// Consume the `Status` stream, running one lease at a time. The stream doubles as
/// the liveness signal, so it is reopened on close; an active lease survives a
/// reconnect.
#[allow(clippy::too_many_arguments)]
async fn status_loop(
    mut controller: Controller,
    status_tx: Arc<watch::Sender<StatusSnapshot>>,
    routing_tx: watch::Sender<Option<Arc<RoutingTable>>>,
    end_session_tx: watch::Sender<Option<Arc<Notify>>>,
    main_uds: &str,
    hook_uds: &str,
    initial_host: SlimHost,
    config_path: &Path,
    config: &ExporterConfig,
    hook_log: Arc<crate::logbuf::HookLog>,
    shutdown: Arc<Notify>,
) -> Result<(), Error> {
    let mut active: Option<ActiveLease> = None;
    // The first lease reuses the registration host; later leases use one pre-warmed
    // during the previous lease.
    let mut warm = Warm::Ready(initial_host);

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
                // A new lease while idle: take the pre-warmed host, swap in its routing,
                // and start the lifecycle — all before the beforeLease hook runs. Taking
                // the host kicks off pre-warming the next one for the lease after this.
                (true, false) => {
                    let lease_name = resp.lease_name.clone().unwrap();
                    let client_name = resp.client_name.clone().unwrap_or_default();
                    tracing::info!(lease = %lease_name, client = %client_name, "lease started");

                    let host = warm.take(config_path).await?;
                    let routing = RoutingTable::build(uds_channel(host.socket()).await?).await?;
                    routing_tx.send_replace(Some(Arc::new(routing)));

                    let end = Arc::new(Notify::new());
                    let end_session = Arc::new(Notify::new());
                    end_session_tx.send_replace(Some(end_session.clone()));

                    let handle = spawn_lease(
                        StatusReporter::new(controller.clone(), status_tx.clone()),
                        lease_name,
                        client_name,
                        main_uds.to_string(),
                        hook_uds.to_string(),
                        config.tls.clone(),
                        config.hooks.clone(),
                        hook_log.clone(),
                        end.clone(),
                        end_session,
                        shutdown.clone(),
                    );
                    active = Some(ActiveLease { handle, end, host });
                }
                // The active lease ended: signal it, wait for cleanup (afterLease) to
                // finish, then clear routing and kill the host before the next lease.
                (false, true) => {
                    tracing::info!("lease ended");
                    let lease = active.take().unwrap();
                    lease.end.notify_one();
                    let _ = lease.handle.await;
                    routing_tx.send_replace(None);
                    end_session_tx.send_replace(None);
                    drop(lease.host); // SIGKILLs the slim host subprocess
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
    mut reporter: StatusReporter,
    lease_name: String,
    client_name: String,
    main_socket: String,
    hook_socket: String,
    tls: TlsConfig,
    hooks: HookConfig,
    hook_log: Arc<crate::logbuf::HookLog>,
    end: Arc<Notify>,
    end_session: Arc<Notify>,
    shutdown: Arc<Notify>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let ctx = HookContext {
            hook_socket: &hook_socket,
            lease_name: &lease_name,
            client_name: &client_name,
            hook_log,
        };
        let mut lc = LeaseLifecycle::new();
        let has_client = Arc::new(AtomicBool::new(false));

        advance(&mut lc, LeasePhase::Starting);

        // Open the router Listen stream *before* running beforeLease so the exporter is
        // reachable while the hook runs. The controller permits client Dials during
        // `BeforeLeaseHook`/`AfterLeaseHook` (not just `LeaseReady`) — see
        // `controller_service.go:checkExporterStatusForDriverCalls` — and the Python
        // exporter likewise establishes session+Listen before LEASE_READY
        // (`exporter.py:serve`). This lets a client observe the lease's
        // `GetStatus`/`LogStream` (including a beforeLease failure) during the hook.
        let listen = spawn_listen(
            reporter.controller().clone(),
            lease_name.clone(),
            main_socket,
            tls,
            has_client.clone(),
        );

        // --- beforeLease -------------------------------------------------------
        let before_hook = hooks.before_lease.as_ref();
        if before_hook.is_some() {
            advance(&mut lc, LeasePhase::BeforeLease);
        }
        let reason = match hooks::run_before_lease(&mut reporter, before_hook, &ctx).await {
            BeforeOutcome::Exit => {
                // beforeLease already reported BEFORE_LEASE_HOOK_FAILED + OFFLINE.
                listen.abort();
                advance(&mut lc, LeasePhase::Failed);
                shutdown.notify_one();
                return;
            }
            BeforeOutcome::Ready => {
                advance(&mut lc, LeasePhase::Ready);
                // Serve until the controller ends the lease OR the client ends it early.
                let reason = tokio::select! {
                    _ = end.notified() => EndReason::Controller,
                    _ = end_session.notified() => EndReason::EndSession,
                };
                listen.abort();
                advance(&mut lc, LeasePhase::Ending);
                reason
            }
            BeforeOutcome::EndLease => {
                listen.abort();
                advance(&mut lc, LeasePhase::Ending);
                EndReason::EndLease
            }
        };

        // --- afterLease (when a client used the board, or the client ended early) ---
        let run_after = has_client.load(Ordering::Relaxed) || reason == EndReason::EndSession;
        let after_hook = if run_after {
            hooks.after_lease.as_ref()
        } else {
            None
        };
        if after_hook.is_some() {
            advance(&mut lc, LeasePhase::AfterLease);
            match hooks::run_after_lease(&mut reporter, after_hook, &ctx).await {
                AfterOutcome::Done => {
                    advance(&mut lc, LeasePhase::Releasing);
                    advance(&mut lc, LeasePhase::Done);
                }
                AfterOutcome::Exit => {
                    advance(&mut lc, LeasePhase::Failed);
                    shutdown.notify_one();
                    return;
                }
            }
        } else {
            // No afterLease hook to run: report availability for the next lease.
            hooks::run_after_lease(&mut reporter, None, &ctx).await;
            advance(&mut lc, LeasePhase::Done);
        }

        // If we ended the lease ourselves (an EndSession or an endLease hook failure,
        // not a controller end), proactively release it. We do NOT block on the
        // controller's `leased=false` here: `status_loop` owns the host and holds it
        // alive until that signal arrives (or the exporter shuts down), so a client's
        // tail (GetStatus / LogStream / a still-open stream) keeps working, and a
        // controller that never confirms the release cannot hang this task — mirroring
        // Python's self-signalled fallback (exporter.py:405-409).
        if reason != EndReason::Controller {
            reporter
                .request_release("Lease released after session end")
                .await;
        }
    })
}

/// Open `Listen` and terminate each incoming client tunnel into the local Rust
/// `ExporterService` server's main socket (design §6.3 option B: reuse the byte
/// bridge, but its target is now our own tonic server instead of the Python
/// session). Records the first connection request so the lifecycle knows the board
/// was used.
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
