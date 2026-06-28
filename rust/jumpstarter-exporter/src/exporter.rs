//! The exporter runtime loop (spec doc 03; native-migration §4).
//!
//! Registers with the controller (via a throwaway host), then consumes the
//! server-streaming `Status` RPC and serves one lease at a time. Each lease spawns a
//! **fresh** slim host (fresh drivers — `exporter.py:577-593`) during `Starting`,
//! swaps its routing into the process-lifetime session server, and hands the lease to the
//! [`crate::lease_runner`], which **drives** the [`crate::lease_fsm`] typestate machine (its
//! effects — `beforeLease`/`afterLease` [`crate::hooks`], `Listen`, status reports — supplied
//! by [`crate::controller_effects`]), then kills the host at lease end. A client `EndSession`
//! ends the lease early (running afterLease). On shutdown — a signal or an `on_failure: exit`
//! hook — it reports `OFFLINE` and unregisters.
//!
//! Deferred to later increments: the supervisor fork/restart loop + rapid-failure
//! breaker, the `_retry_stream` contract (5×1.0 s), standalone TCP, and Rust-side
//! LogStream aggregation.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_lease::channel;
use jumpstarter_config::ExporterConfig;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::{ExporterStatus, RegisterRequest, StatusRequest, UnregisterRequest};
use tokio::sync::{watch, Notify};
use tokio::task::JoinHandle;
use tokio::time::sleep;
use tokio_stream::StreamExt as _;

use jumpstarter_fsm::{Envelope, Mailbox};

use crate::backend::{DriverBackend, HostFactory, HostGuard};
use crate::control::{Controller, StatusReporter, StatusSnapshot};
use crate::controller_effects::ControllerEffects;
use crate::lease_fsm::{ClientSignal, ControllerSignal, LeaseConfig, LeaseContext, LeaseSignal};
use crate::lease_runner::run_lease;
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

/// Why the exporter's serve loop returned, so the host can decide whether to restart it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExporterExit {
    /// A shutdown signal (SIGINT/SIGTERM) or an `on_failure: exit` hook — terminate,
    /// do NOT restart.
    Shutdown,
    /// The serve loop returned on its own (e.g. the controller stream ended) — restartable.
    Completed,
}

/// Run the exporter until a shutdown signal (SIGINT/SIGTERM) or an `on_failure: exit`
/// hook. Drivers are hosted via the polyglot hub: one subprocess per top-level `export:`
/// entry, in the entry's `runtime` (Python or native Rust).
pub async fn run(opts: RunOptions) -> Result<(), Error> {
    let factory = Arc::new(crate::polyglot::PolyglotHostFactory::new(opts.config_path));
    run_with_factory(opts.config, factory).await.map(|_| ())
}

/// Run the exporter against any driver-host [`HostFactory`] — the generic entry the
/// in-process (foreign) host injects. [`run`] is the slim-subprocess wrapper.
pub async fn run_with_factory(
    config: ExporterConfig,
    factory: Arc<dyn HostFactory>,
) -> Result<ExporterExit, Error> {
    let config = &config;
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
    tracing::debug!(%name, "provisioning first driver host for registration");
    let (first_backend, first_guard) = factory.provision().await?;
    let registration = RoutingTable::build(first_backend.clone())
        .await?
        .report()
        .clone();
    tracing::debug!(%name, drivers = registration.reports.len(), "first host provisioned; registering with controller");

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
    let outcome: Result<ExporterExit, Error> = tokio::select! {
        r = status_loop(
            controller.clone(),
            status_tx,
            routing_tx,
            end_session_tx,
            &main_uds,
            &hook_uds,
            (first_backend, first_guard),
            factory.clone(),
            config,
            hook_log,
            shutdown.clone(),
        ) => r.map(|()| ExporterExit::Completed),
        _ = shutdown.notified() => {
            tracing::info!("exporter shutdown requested by hook");
            Ok(ExporterExit::Shutdown)
        }
        _ = shutdown_signal() => {
            tracing::info!("shutdown signal received");
            Ok(ExporterExit::Shutdown)
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
    /// Bridges the controller-end / client-EndSession Notifies into FSM facts; aborted at end.
    pump: JoinHandle<()>,
    /// Held for the lease lifetime; dropped at lease end to tear the host down.
    _guard: Box<dyn HostGuard>,
}

/// A provisioned host: the backend to route into + its lease guard.
type ProvisionedHost = (Arc<dyn DriverBackend>, Box<dyn HostGuard>);

/// A pre-warmed host for the *next* lease: either ready, or still provisioning in the
/// background. Pipelining provisioning (started during the previous lease) hides the
/// cold-start latency (interpreter + driver imports) so a lease rarely pays for it.
enum Warm {
    Ready(ProvisionedHost),
    Spawning(JoinHandle<Result<ProvisionedHost, Error>>),
}

impl Warm {
    /// Start provisioning a fresh host in the background.
    fn spawn(factory: Arc<dyn HostFactory>) -> Self {
        Warm::Spawning(tokio::spawn(async move { factory.provision().await }))
    }

    /// Take the warmed host (awaiting the in-flight provisioning if it hasn't finished
    /// yet) and immediately kick off warming the *next* one.
    async fn take(&mut self, factory: &Arc<dyn HostFactory>) -> Result<ProvisionedHost, Error> {
        match std::mem::replace(self, Warm::spawn(factory.clone())) {
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
    initial: ProvisionedHost,
    factory: Arc<dyn HostFactory>,
    config: &ExporterConfig,
    hook_log: Arc<crate::logbuf::HookLog>,
    shutdown: Arc<Notify>,
) -> Result<(), Error> {
    let mut active: Option<ActiveLease> = None;
    // The first lease reuses the registration host; later leases use one pre-warmed
    // during the previous lease.
    let mut warm = Warm::Ready(initial);

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
            tracing::debug!(
                leased,
                lease = ?resp.lease_name,
                client = ?resp.client_name,
                "Status update"
            );

            match (leased, active.is_some()) {
                // A new lease while idle: take the pre-warmed host, swap in its routing,
                // and start the lifecycle — all before the beforeLease hook runs. Taking
                // the host kicks off pre-warming the next one for the lease after this.
                (true, false) => {
                    let lease_name = resp.lease_name.clone().unwrap();
                    let client_name = resp.client_name.clone().unwrap_or_default();
                    tracing::info!(lease = %lease_name, client = %client_name, "lease started");

                    // Provision the per-lease host + routing. A failure here (driver-host import
                    // error, transient spawn) must NOT tear down the exporter for every future
                    // lease: log it and skip *this* lease — the controller re-drives the lease via
                    // the Status stream, and `warm.take` has already kicked off a fresh host.
                    let (backend, guard) = match warm.take(&factory).await {
                        Ok(h) => h,
                        Err(e) => {
                            tracing::error!(lease = %lease_name, error = %e, "provisioning host for lease failed; skipping this lease");
                            continue;
                        }
                    };
                    let routing = match RoutingTable::build(backend).await {
                        Ok(r) => r,
                        Err(e) => {
                            tracing::error!(lease = %lease_name, error = %e, "building routing for lease failed; skipping this lease");
                            continue;
                        }
                    };
                    routing_tx.send_replace(Some(Arc::new(routing)));

                    let end = Arc::new(Notify::new());
                    let end_session = Arc::new(Notify::new());
                    end_session_tx.send_replace(Some(end_session.clone()));

                    // The lease's signal mailbox: the runner clones `tx` into each effect's
                    // origin-typed sink; a small pump turns the controller-end / client-EndSession
                    // Notifies into `Controller(Ended)` / `Client(EndSession)` facts.
                    let (tx, mailbox) = Mailbox::<LeaseSignal>::channel();
                    let pump = {
                        let pump_tx = tx.clone();
                        let end_p = end.clone();
                        let es_p = end_session.clone();
                        tokio::spawn(async move {
                            tokio::select! {
                                _ = end_p.notified() => {
                                    let _ = pump_tx.send(Envelope::new(LeaseSignal::Controller(
                                        ControllerSignal::Ended,
                                    )));
                                }
                                _ = es_p.notified() => {
                                    let _ = pump_tx.send(Envelope::new(LeaseSignal::Client(
                                        ClientSignal::EndSession,
                                    )));
                                }
                            }
                        })
                    };

                    let effects = ControllerEffects::new(
                        StatusReporter::new(controller.clone(), status_tx.clone()),
                        controller.clone(),
                        main_uds.to_string(),
                        hook_uds.to_string(),
                        config.tls.clone(),
                        config.hooks.clone(),
                        hook_log.clone(),
                        shutdown.clone(),
                    );
                    let lease_ctx = LeaseContext {
                        lease_name,
                        client_name,
                        config: LeaseConfig::default(),
                    };
                    let handle = tokio::spawn(async move {
                        run_lease(effects, lease_ctx, tx, mailbox).await;
                    });
                    active = Some(ActiveLease {
                        handle,
                        end,
                        pump,
                        _guard: guard,
                    });
                }
                // The active lease ended: signal it, wait for cleanup (afterLease) to
                // finish, then clear routing and kill the host before the next lease.
                (false, true) => {
                    tracing::info!("lease ended");
                    let lease = active.take().unwrap();
                    lease.end.notify_one(); // -> pump -> Controller(Ended) fact -> runner
                    // Bound the wait for the runner to finish its teardown (afterLease etc.): a
                    // hung runner must not wedge the Status loop — and hence the whole exporter —
                    // forever. The runner should finish within the afterLease hook budget; abort
                    // and proceed with host teardown if it overruns.
                    let lease_grace = Duration::from_secs(
                        config
                            .hooks
                            .after_lease
                            .as_ref()
                            .map(|h| h.timeout.max(0) as u64)
                            .unwrap_or(0)
                            + 60,
                    );
                    let mut handle = lease.handle;
                    if tokio::time::timeout(lease_grace, &mut handle).await.is_err() {
                        tracing::error!(grace = ?lease_grace, "lease runner did not finish in time; aborting it");
                        handle.abort();
                    }
                    lease.pump.abort();
                    routing_tx.send_replace(None);
                    end_session_tx.send_replace(None);
                    drop(lease._guard); // tears down the per-driver hosts (subprocess SIGKILL / foreign close)
                    sleep(INTER_LEASE_SETTLE).await;
                    tracing::info!("exporter available for next lease");
                }
                // Steady state (still leased, or still idle): nothing to do.
                _ => {}
            }
        }

        tracing::debug!("Status stream closed; reconnecting");
        sleep(Duration::from_secs(1)).await;
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
