//! The production [`LeaseEffects`] implementation that backs [`crate::lease_runner::run_lease`].
//!
//! The lease runner drives the lifecycle *structure* and reports the **clean-phase** statuses
//! (`BEFORE_LEASE_HOOK` / `LEASE_READY` / `AFTER_LEASE_HOOK` / `AVAILABLE`) via the FSM
//! projection; this effects layer renders everything else against the real machinery: it opens
//! `Listen` and bridges client tunnels, runs the hook subprocesses (reporting their *failure*
//! statuses with the hook's message), drains/holds where the lifecycle directs, and releases /
//! shuts down. Hook subprocesses post origin-typed `Hook(..)` facts back to the runner.

use std::sync::Arc;
use std::time::{Duration, Instant};

use jumpstarter_lease::router;
use jumpstarter_config::{HookConfig, TlsConfig};
use jumpstarter_protocol::v1::{ExporterStatus, ListenRequest, LogSource};
use tokio::net::UnixStream;
use tokio::sync::Notify;
use tokio::time::sleep;
use tokio_stream::StreamExt as _;

use jumpstarter_fsm::SignalSink;

use crate::control::{Controller, ReportOutcome, StatusReporter};
use crate::hooks::{run_hook, HookContext, HookOutcome};
use crate::lease_fsm::{ClientSignal, HookResult, HookSignal, LeaseContext, LeaseSignal};
use crate::lease_runner::LeaseEffects;
use crate::logbuf::HookLog;

/// How long to keep `LEASE_READY` observable when the lease ended *during* a slow `beforeLease`
/// (its duration expired under the hook). Must exceed the client's `GetStatus` poll (150 ms) so a
/// client still polling observes `LEASE_READY` instead of racing to `AVAILABLE` (#569).
const LEASE_READY_GRACE: Duration = Duration::from_millis(500);

/// Prefix on warn-mode hook messages (`common/__init__.py:12`).
const HOOK_WARNING_PREFIX: &str = "[HOOK_WARNING] ";

/// Guarantees a terminal hook fact reaches the runner even if the hook task unwinds (panics)
/// before posting one — otherwise the runner's `BeforeLease`/`AfterLease` wait would hang
/// forever. On `Drop` without an explicit [`Self::post`], it reports the hook as `Panicked`, so
/// the FSM fails the lease cleanly (and the exporter shuts down) instead of stalling.
struct HookDonePost {
    sink: SignalSink<HookSignal, LeaseSignal>,
    before: bool,
    posted: bool,
}

impl HookDonePost {
    fn before(sink: SignalSink<HookSignal, LeaseSignal>) -> Self {
        Self {
            sink,
            before: true,
            posted: false,
        }
    }

    fn after(sink: SignalSink<HookSignal, LeaseSignal>) -> Self {
        Self {
            sink,
            before: false,
            posted: false,
        }
    }

    fn post(&mut self, result: HookResult) {
        self.posted = true;
        let signal = if self.before {
            HookSignal::BeforeDone(result)
        } else {
            HookSignal::AfterDone(result)
        };
        self.sink.send(signal);
    }
}

impl Drop for HookDonePost {
    fn drop(&mut self) {
        if !self.posted {
            self.post(HookResult::Panicked);
        }
    }
}

/// The canonical status message the Python exporter used for each clean-phase status.
fn canonical_message(status: ExporterStatus) -> &'static str {
    match status {
        ExporterStatus::BeforeLeaseHook => "Running beforeLease hook",
        ExporterStatus::LeaseReady => "Ready for commands",
        ExporterStatus::AfterLeaseHook => "Running afterLease hooks",
        ExporterStatus::Available => "Available for new lease",
        _ => "",
    }
}

/// Effects for one lease, wired to the controller channel, the session sockets, and the hooks.
pub struct ControllerEffects {
    reporter: StatusReporter,
    controller: Controller,
    main_socket: String,
    hook_socket: String,
    tls: TlsConfig,
    hooks: HookConfig,
    hook_log: Arc<HookLog>,
    /// Notified on a terminal failure (an `on_failure: exit` hook) — the top-level serve loop
    /// then reports `OFFLINE` and unregisters.
    shutdown: Arc<Notify>,
}

impl ControllerEffects {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        reporter: StatusReporter,
        controller: Controller,
        main_socket: String,
        hook_socket: String,
        tls: TlsConfig,
        hooks: HookConfig,
        hook_log: Arc<HookLog>,
        shutdown: Arc<Notify>,
    ) -> Self {
        Self {
            reporter,
            controller,
            main_socket,
            hook_socket,
            tls,
            hooks,
            hook_log,
            shutdown,
        }
    }
}

impl LeaseEffects for ControllerEffects {
    async fn report_status(&mut self, status: ExporterStatus, message: &str) -> ReportOutcome {
        let msg = match status {
            ExporterStatus::BeforeLeaseHook
            | ExporterStatus::LeaseReady
            | ExporterStatus::AfterLeaseHook
            | ExporterStatus::Available => canonical_message(status),
            _ => message,
        };
        self.reporter.try_report(status, msg).await
    }

    fn spawn_listen(&mut self, ctx: &LeaseContext, sink: SignalSink<ClientSignal, LeaseSignal>) {
        // Open `Listen` *before* beforeLease so the exporter is reachable while the hook runs
        // (the controller permits Dials during BEFORE_LEASE_HOOK). Each incoming connection
        // posts `Client(Connected)` (idempotent in the FSM) and bridges into the local server.
        let mut controller = self.controller.clone();
        let lease_name = ctx.lease_name.clone();
        let main_socket = self.main_socket.clone();
        let tls = self.tls.clone();
        tokio::spawn(async move {
            // Reconnect resiliently: a controller restart / network blip drops the Listen stream,
            // and without this the lease would be permanently unreachable for new clients for the
            // rest of its life. Distinguish a CLEAN end (the controller closed the stream because
            // the lease ended → stop) from a transient error (reconnect with bounded backoff). A
            // consecutive-failure budget stops a doomed loop (lease gone *and* controller
            // unreachable) so the task can't leak after the lease is over.
            const MAX_BACKOFF: Duration = Duration::from_secs(2);
            const GIVE_UP_AFTER: Duration = Duration::from_secs(60);
            let mut backoff = Duration::from_millis(100);
            let mut failing_since: Option<Instant> = None;
            loop {
                let mut clean_end = false;
                let mut made_progress = false;
                match controller
                    .listen(ListenRequest {
                        lease_name: lease_name.clone(),
                    })
                    .await
                {
                    Ok(stream) => {
                        let mut listen = stream.into_inner();
                        tracing::info!(lease = %lease_name, "awaiting connection requests");
                        loop {
                            match listen.next().await {
                                Some(Ok(resp)) => {
                                    made_progress = true;
                                    sink.send(ClientSignal::Connected);
                                    tracing::debug!(lease = %lease_name, router_endpoint = %resp.router_endpoint, "accepted connection request");
                                    let main_socket = main_socket.clone();
                                    let tls = tls.clone();
                                    let bridge_lease = lease_name.clone();
                                    tokio::spawn(async move {
                                        match UnixStream::connect(&main_socket).await {
                                            Ok(stream) => {
                                                if let Err(e) = router::bridge(
                                                    stream,
                                                    &resp.router_endpoint,
                                                    &resp.router_token,
                                                    &tls,
                                                )
                                                .await
                                                {
                                                    tracing::warn!(lease = %bridge_lease, error = %e, "router bridge failed");
                                                } else {
                                                    tracing::debug!(lease = %bridge_lease, "router bridge completed");
                                                }
                                            }
                                            Err(e) => tracing::warn!(lease = %bridge_lease, error = %e, "connecting to session socket failed"),
                                        }
                                    });
                                }
                                Some(Err(e)) => {
                                    tracing::warn!(lease = %lease_name, error = %e, "Listen stream error; reconnecting");
                                    break;
                                }
                                None => {
                                    // The controller closed the stream cleanly — the lease ended.
                                    tracing::debug!(lease = %lease_name, "Listen stream closed by controller; lease ended");
                                    clean_end = true;
                                    break;
                                }
                            }
                        }
                    }
                    Err(e) => {
                        tracing::warn!(lease = %lease_name, error = %e, "opening Listen stream failed; retrying");
                    }
                }

                if clean_end {
                    return;
                }
                // Transient failure: a round that delivered connections resets the budget;
                // otherwise enforce the consecutive-failure deadline so a doomed loop can't leak.
                if made_progress {
                    backoff = Duration::from_millis(100);
                    failing_since = None;
                } else {
                    let since = *failing_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= GIVE_UP_AFTER {
                        tracing::error!(lease = %lease_name, "Listen stream unrecoverable after 60s; giving up");
                        return;
                    }
                }
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(MAX_BACKOFF);
            }
        });
    }

    fn spawn_before_lease(
        &mut self,
        ctx: &LeaseContext,
        sink: SignalSink<HookSignal, LeaseSignal>,
    ) {
        let Some(hook) = self.hooks.before_lease.clone() else {
            // The runner only enters BeforeLease when a hook is configured; be safe anyway.
            sink.send(HookSignal::BeforeDone(HookResult::Ok));
            return;
        };
        let hook_socket = self.hook_socket.clone();
        let lease_name = ctx.lease_name.clone();
        let client_name = ctx.client_name.clone();
        let hook_log = self.hook_log.clone();
        let mut reporter = self.reporter.clone();
        tokio::spawn(async move {
            tracing::debug!(lease = %lease_name, client = %client_name, "beforeLease hook effect started");
            // Posts BeforeDone on Drop (incl. a panic unwind) so the runner never hangs.
            let mut done = HookDonePost::before(sink);
            let hctx = HookContext {
                hook_socket: &hook_socket,
                lease_name: &lease_name,
                client_name: &client_name,
                hook_log: hook_log.clone(),
            };
            let result = match run_hook(&hook, &hctx, LogSource::BeforeLeaseHook).await {
                HookOutcome::Success => HookResult::Ok,
                HookOutcome::Warn(w) => {
                    hook_log.push(
                        LogSource::BeforeLeaseHook,
                        format!("{HOOK_WARNING_PREFIX}beforeLease hook warning: {w}"),
                    );
                    HookResult::Ok
                }
                HookOutcome::EndLease(e) => {
                    reporter
                        .report(
                            ExporterStatus::BeforeLeaseHookFailed,
                            &format!("beforeLease hook failed (on_failure=endLease): {e}"),
                        )
                        .await;
                    HookResult::EndLease
                }
                HookOutcome::Exit(e) => {
                    reporter
                        .report(
                            ExporterStatus::BeforeLeaseHookFailed,
                            &format!("beforeLease hook failed (on_failure=exit, shutting down): {e}"),
                        )
                        .await;
                    HookResult::Failed
                }
            };
            tracing::debug!(lease = %lease_name, client = %client_name, ?result, "beforeLease hook effect finished");
            done.post(result);
        });
    }

    fn spawn_after_lease(&mut self, ctx: &LeaseContext, sink: SignalSink<HookSignal, LeaseSignal>) {
        let Some(hook) = self.hooks.after_lease.clone() else {
            sink.send(HookSignal::AfterDone(HookResult::Ok));
            return;
        };
        let hook_socket = self.hook_socket.clone();
        let lease_name = ctx.lease_name.clone();
        let client_name = ctx.client_name.clone();
        let hook_log = self.hook_log.clone();
        let mut reporter = self.reporter.clone();
        tokio::spawn(async move {
            tracing::debug!(lease = %lease_name, client = %client_name, "afterLease hook effect started");
            // Posts AfterDone on Drop (incl. a panic unwind) so the runner never hangs.
            let mut done = HookDonePost::after(sink);
            let hctx = HookContext {
                hook_socket: &hook_socket,
                lease_name: &lease_name,
                client_name: &client_name,
                hook_log: hook_log.clone(),
            };
            let result = match run_hook(&hook, &hctx, LogSource::AfterLeaseHook).await {
                HookOutcome::Success => HookResult::Ok,
                HookOutcome::Warn(w) => {
                    hook_log.push(
                        LogSource::AfterLeaseHook,
                        format!("{HOOK_WARNING_PREFIX}afterLease hook warning: {w}"),
                    );
                    HookResult::Ok
                }
                HookOutcome::EndLease(e) => {
                    reporter
                        .report(
                            ExporterStatus::AfterLeaseHookFailed,
                            &format!("afterLease hook failed (on_failure=endLease): {e}"),
                        )
                        .await;
                    HookResult::EndLease
                }
                HookOutcome::Exit(e) => {
                    reporter
                        .report(
                            ExporterStatus::AfterLeaseHookFailed,
                            &format!("afterLease hook failed (on_failure=exit, shutting down): {e}"),
                        )
                        .await;
                    HookResult::Failed
                }
            };
            tracing::debug!(lease = %lease_name, client = %client_name, ?result, "afterLease hook effect finished");
            done.post(result);
        });
    }

    async fn drain_connections(&mut self) {
        // DD-8: real connection draining is a follow-up (needs tracked bridge tasks). Today the
        // host SIGKILL + inter-lease settle in `status_loop` covers teardown; this is a no-op so
        // behaviour is unchanged.
    }

    async fn lease_ready_grace(&mut self) {
        sleep(LEASE_READY_GRACE).await;
    }

    async fn request_release(&mut self, message: &str) {
        self.reporter.request_release(message).await;
    }

    async fn shutdown(&mut self, _message: &str) {
        // The top-level serve loop reports OFFLINE + unregisters; just trip the signal.
        self.shutdown.notify_one();
    }

    fn has_before_lease_hook(&self) -> bool {
        self.hooks.before_lease.is_some()
    }

    fn has_after_lease_hook(&self) -> bool {
        self.hooks.after_lease.is_some()
    }
}
