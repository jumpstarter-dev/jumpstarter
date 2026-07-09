//! The single-owner runner that **drives** the lease typestate FSM ([`crate::lease_fsm`]).
//!
//! One task owns the [`LeaseState`]. The runner is a uniform loop: it gates the just-entered
//! state's projected status on the controller (DD-7), then runs that state's entry effects and
//! advances. Internal progressions (`Created→Starting`, `Ending→Releasing`, …) are straight-line
//! typed calls; waiting states (`BeforeLease`, `Ready`, `AfterLease`) block on the mailbox and
//! feed each [`LeaseSignal`] *fact* to the typed `apply`, which can only produce a valid edge.
//! Because every transition funnels through this one task, concurrent transition attempts are
//! structurally impossible.
//!
//! All tonic / subprocess / socket work lives behind the [`LeaseEffects`] port, so the runner
//! stays pure-ish and unit-testable with a mock. Effects post origin-typed facts back through
//! [`SignalSink`]s; an effect can only emit facts from its own origin (a hook task cannot post a
//! controller signal).

use jumpstarter_fsm::{ack, Envelope, Fsm, Mailbox, Outcome, SignalSink};
use jumpstarter_protocol::v1::ExporterStatus;
use tokio::sync::mpsc::UnboundedSender;

use crate::control::ReportOutcome;
use crate::lease_fsm::{
    project, ClientSignal, HookSignal, LeaseContext, LeaseEndReason, LeaseFailureReason,
    LeaseSignal, LeaseState,
};

/// A mailbox sender for one lease's signals (shared by the runner and every effect sink).
pub type LeaseSender = UnboundedSender<Envelope<LeaseSignal>>;

/// The effects port: everything the runner needs from the outside world (controller reports,
/// hook subprocesses, the Listen accept loop, connection draining, release, shutdown). The real
/// impl ([`crate::exporter`]'s `ControllerEffects`, added at the cutover) wires these to
/// `hooks`/`control`/Listen; a mock drives unit tests.
#[allow(async_fn_in_trait)]
pub trait LeaseEffects {
    /// Report a clean-phase status to the controller; the controller may reject it (DD-7).
    async fn report_status(&mut self, status: ExporterStatus, message: &str) -> ReportOutcome;

    /// Open the Listen accept loop; on the first client connection, post `Client(Connected)`.
    fn spawn_listen(&mut self, ctx: &LeaseContext, sink: SignalSink<ClientSignal, LeaseSignal>);

    /// Run the before-lease hook (reporting its own `*_FAILED` status on failure) and post
    /// `Hook(BeforeDone(result))` when it finishes.
    fn spawn_before_lease(&mut self, ctx: &LeaseContext, sink: SignalSink<HookSignal, LeaseSignal>);

    /// Run the after-lease hook and post `Hook(AfterDone(result))` when it finishes.
    fn spawn_after_lease(&mut self, ctx: &LeaseContext, sink: SignalSink<HookSignal, LeaseSignal>);

    /// Drain in-flight dial tokens / open connections before the after-lease phase (DD-8).
    async fn drain_connections(&mut self);

    /// Hold `LEASE_READY` briefly when the lease ended *during* a slow `beforeLease`, so a client
    /// still polling `GetStatus` observes `LEASE_READY` rather than racing to `AVAILABLE` (#569).
    async fn lease_ready_grace(&mut self);

    /// Ask the controller to release the lease (when the exporter, not the controller, ended it).
    async fn request_release(&mut self, message: &str);

    /// Failure / teardown: report `OFFLINE` and signal exporter shutdown.
    async fn shutdown(&mut self, message: &str);

    /// Whether a before-lease hook is configured (decides `Starting → BeforeLease` vs `Ready`).
    fn has_before_lease_hook(&self) -> bool;

    /// Whether an after-lease hook is configured.
    fn has_after_lease_hook(&self) -> bool;
}

/// Drive one lease to a terminal state. `tx`/`mailbox` are the lease's signal channel: the
/// caller keeps a `SignalSink<ControllerSignal, _>` (or `SystemSignal` for shutdown) cloned from
/// `tx` to post external facts; the runner clones `tx` into each effect's origin-typed sink.
/// Returns the terminal [`LeaseState`] (`Done` or `Failed`).
pub async fn run_lease<E: LeaseEffects>(
    mut fx: E,
    ctx: LeaseContext,
    tx: LeaseSender,
    mut mailbox: Mailbox<LeaseSignal>,
) -> LeaseState {
    // The exporter is `AVAILABLE` before a lease, so a `Created`/`Starting` projection of
    // `Available` is already current and need not be re-reported.
    let mut last = Some(ExporterStatus::Available);
    let mut state = LeaseState::initial(ctx);

    loop {
        // DD-7 gate: report the clean-phase status of the just-entered state. Failure statuses
        // (`*_HOOK_FAILED`) are reported by the hook effects, so we skip `Failed` here.
        if !matches!(state, LeaseState::Failed(_)) {
            if let Some(status) = project(&state) {
                if last != Some(status) {
                    match fx.report_status(status, "").await {
                        ReportOutcome::Accepted => last = Some(status),
                        ReportOutcome::Rejected(_) => {
                            // Must not serve in a status the controller rejected: fail cleanly.
                            state = force_fail(state);
                            continue;
                        }
                    }
                }
            }
        }

        if state.is_terminal() {
            if matches!(state, LeaseState::Failed(_)) {
                fx.shutdown("lease failed").await;
            }
            break;
        }

        state = advance(&mut fx, state, &tx, &mut mailbox).await;
    }
    state
}

/// Run the current state's entry effects and return the next state.
async fn advance<E: LeaseEffects>(
    fx: &mut E,
    state: LeaseState,
    tx: &LeaseSender,
    mailbox: &mut Mailbox<LeaseSignal>,
) -> LeaseState {
    match state {
        // Internal progressions — straight-line typed calls.
        LeaseState::Created(s) => s.begin_setup().into(),
        LeaseState::Starting(s) => {
            // Open Listen before the (possibly slow) before-hook, so a client can start dialing.
            fx.spawn_listen(s.ctx(), SignalSink::new(tx.clone()));
            if fx.has_before_lease_hook() {
                s.enter_before_lease().into()
            } else {
                s.mark_ready().into()
            }
        }
        LeaseState::Ending(s) => {
            fx.drain_connections().await;
            if s.should_run_after_lease() && fx.has_after_lease_hook() {
                s.enter_after_lease().into()
            } else {
                s.begin_release().into()
            }
        }
        LeaseState::Releasing(s) => {
            // The controller already knows when it initiated the end; otherwise tell it.
            if s.reason() != LeaseEndReason::Controller {
                fx.request_release("lease released").await;
            }
            s.mark_done().into()
        }

        // Waiting states — spawn the entry effect once, then block on facts until the variant
        // changes (self-loops like `Client(Connected)` / a deferred end keep us waiting).
        LeaseState::BeforeLease(s) => {
            fx.spawn_before_lease(s.ctx(), SignalSink::new(tx.clone()));
            wait_until_change(mailbox, s.into()).await
        }
        LeaseState::Ready(s) => {
            if s.deferred_end().is_some() {
                // The lease ended during a slow beforeLease; LEASE_READY was already reported by
                // the gate — hold it briefly, then end (the runner, not a signal, decides).
                fx.lease_ready_grace().await;
                s.end_deferred().into()
            } else {
                wait_until_change(mailbox, s.into()).await
            }
        }
        LeaseState::AfterLease(s) => {
            fx.spawn_after_lease(s.ctx(), SignalSink::new(tx.clone()));
            wait_until_change(mailbox, s.into()).await
        }

        // Terminal states never reach here (the loop breaks first).
        terminal => terminal,
    }
}

/// Block on the mailbox, applying each fact, until the wrapper variant changes (a real
/// transition). Self-loops (same variant, e.g. data-only updates) are acked `Ignored` and keep
/// waiting. If all senders drop while waiting, fail cleanly rather than hang.
async fn wait_until_change(
    mailbox: &mut Mailbox<LeaseSignal>,
    mut state: LeaseState,
) -> LeaseState {
    let start = std::mem::discriminant(&state);
    loop {
        match mailbox.recv().await {
            Some(Envelope { signal, reply }) => {
                let before = std::mem::discriminant(&state);
                let next = state.apply(signal);
                let outcome = if std::mem::discriminant(&next) == before {
                    Outcome::Ignored
                } else {
                    Outcome::Committed
                };
                ack(reply, outcome);
                state = next;
                if std::mem::discriminant(&state) != start {
                    return state;
                }
            }
            None => return force_fail(state),
        }
    }
}

/// Convert any live state to `Failed(Internal)` (used on DD-7 rejection or a lost mailbox).
fn force_fail(state: LeaseState) -> LeaseState {
    use LeaseFailureReason::Internal;
    match state {
        LeaseState::Created(s) => s.fail(Internal).into(),
        LeaseState::Starting(s) => s.fail(Internal).into(),
        LeaseState::BeforeLease(s) => s.fail(Internal).into(),
        LeaseState::Ready(s) => s.fail(Internal).into(),
        LeaseState::Ending(s) => s.fail(Internal).into(),
        LeaseState::AfterLease(s) => s.fail(Internal).into(),
        LeaseState::Releasing(s) => s.fail(Internal).into(),
        terminal => terminal,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lease_fsm::{ControllerSignal, HookResult, LeaseConfig};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    fn ctx() -> LeaseContext {
        LeaseContext {
            lease_name: "demo".into(),
            client_name: "client".into(),
            config: LeaseConfig::default(),
        }
    }

    #[derive(Clone, Copy)]
    enum EndKind {
        Controller,
        EndSession,
    }

    #[derive(Clone)]
    struct Cfg {
        has_before: bool,
        has_after: bool,
        before: HookResult,
        after: HookResult,
        connect_client: bool,
        end: EndKind,
        reject: Option<ExporterStatus>,
        /// Post `Controller(Ended)` *while* the before-hook runs (the #569 deferred-end race).
        end_during_before: bool,
    }

    impl Default for Cfg {
        fn default() -> Self {
            Self {
                has_before: true,
                has_after: true,
                before: HookResult::Ok,
                after: HookResult::Ok,
                connect_client: true,
                end: EndKind::Controller,
                reject: None,
                end_during_before: false,
            }
        }
    }

    #[derive(Clone)]
    struct Handles {
        reported: Arc<Mutex<Vec<ExporterStatus>>>,
        released: Arc<AtomicBool>,
        shutdown: Arc<AtomicBool>,
    }

    struct MockEffects {
        tx: LeaseSender,
        cfg: Cfg,
        reported: Arc<Mutex<Vec<ExporterStatus>>>,
        released: Arc<AtomicBool>,
        shutdown: Arc<AtomicBool>,
        end_posted: AtomicBool,
    }

    fn mock(cfg: Cfg) -> (MockEffects, Handles, LeaseSender, Mailbox<LeaseSignal>) {
        let (tx, mailbox) = Mailbox::<LeaseSignal>::channel();
        let h = Handles {
            reported: Arc::new(Mutex::new(Vec::new())),
            released: Arc::new(AtomicBool::new(false)),
            shutdown: Arc::new(AtomicBool::new(false)),
        };
        let m = MockEffects {
            tx: tx.clone(),
            cfg,
            reported: h.reported.clone(),
            released: h.released.clone(),
            shutdown: h.shutdown.clone(),
            end_posted: AtomicBool::new(false),
        };
        (m, h, tx, mailbox)
    }

    impl LeaseEffects for MockEffects {
        async fn report_status(&mut self, status: ExporterStatus, _message: &str) -> ReportOutcome {
            if self.cfg.reject == Some(status) {
                return ReportOutcome::Rejected("test-rejected".into());
            }
            self.reported.lock().unwrap().push(status);
            // Simulate the lease ending right after it becomes ready.
            if status == ExporterStatus::LeaseReady && !self.end_posted.swap(true, Ordering::SeqCst)
            {
                let sig = match self.cfg.end {
                    EndKind::Controller => LeaseSignal::Controller(ControllerSignal::Ended),
                    EndKind::EndSession => LeaseSignal::Client(ClientSignal::EndSession),
                };
                let _ = self.tx.send(Envelope::new(sig));
            }
            ReportOutcome::Accepted
        }

        fn spawn_listen(
            &mut self,
            _ctx: &LeaseContext,
            sink: SignalSink<ClientSignal, LeaseSignal>,
        ) {
            if self.cfg.connect_client {
                sink.send(ClientSignal::Connected);
            }
        }

        fn spawn_before_lease(
            &mut self,
            _ctx: &LeaseContext,
            sink: SignalSink<HookSignal, LeaseSignal>,
        ) {
            if self.cfg.end_during_before {
                // The lease ends while the (slow) hook runs: deferred end recorded in BeforeLease.
                self.end_posted.store(true, Ordering::SeqCst);
                let _ = self.tx.send(Envelope::new(LeaseSignal::Controller(
                    ControllerSignal::Ended,
                )));
            }
            sink.send(HookSignal::BeforeDone(self.cfg.before));
        }

        fn spawn_after_lease(
            &mut self,
            _ctx: &LeaseContext,
            sink: SignalSink<HookSignal, LeaseSignal>,
        ) {
            sink.send(HookSignal::AfterDone(self.cfg.after));
        }

        async fn drain_connections(&mut self) {}

        async fn lease_ready_grace(&mut self) {}

        async fn request_release(&mut self, _message: &str) {
            self.released.store(true, Ordering::SeqCst);
        }

        async fn shutdown(&mut self, _message: &str) {
            self.shutdown.store(true, Ordering::SeqCst);
        }

        fn has_before_lease_hook(&self) -> bool {
            self.cfg.has_before
        }

        fn has_after_lease_hook(&self) -> bool {
            self.cfg.has_after
        }
    }

    async fn run(cfg: Cfg) -> (LeaseState, Handles) {
        let (m, h, tx, mailbox) = mock(cfg);
        let state = tokio::time::timeout(Duration::from_secs(5), run_lease(m, ctx(), tx, mailbox))
            .await
            .expect("run_lease should not hang");
        (state, h)
    }

    #[tokio::test]
    async fn happy_path_with_hooks() {
        let (state, h) = run(Cfg::default()).await;
        assert!(matches!(state, LeaseState::Done(_)));
        assert_eq!(
            *h.reported.lock().unwrap(),
            vec![
                ExporterStatus::BeforeLeaseHook,
                ExporterStatus::LeaseReady,
                ExporterStatus::AfterLeaseHook,
                ExporterStatus::Available,
            ]
        );
        assert!(
            !h.released.load(Ordering::SeqCst),
            "controller-ended: no request_release"
        );
        assert!(!h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn no_hooks_goes_ready_then_available() {
        let cfg = Cfg {
            has_before: false,
            has_after: false,
            connect_client: false,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(matches!(state, LeaseState::Done(_)));
        assert_eq!(
            *h.reported.lock().unwrap(),
            vec![ExporterStatus::LeaseReady, ExporterStatus::Available]
        );
        assert!(!h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn end_session_requests_release() {
        let cfg = Cfg {
            has_before: false,
            has_after: false,
            connect_client: false,
            end: EndKind::EndSession,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(matches!(state, LeaseState::Done(_)));
        assert!(
            h.released.load(Ordering::SeqCst),
            "client-ended: must request_release"
        );
    }

    #[tokio::test]
    async fn before_hook_endlease_releases_without_serving() {
        let cfg = Cfg {
            has_after: false,
            before: HookResult::EndLease,
            connect_client: false,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(
            matches!(state, LeaseState::Done(_)),
            "endLease ends cleanly, no shutdown"
        );
        // Never served: LeaseReady was never reported.
        assert!(!h
            .reported
            .lock()
            .unwrap()
            .contains(&ExporterStatus::LeaseReady));
        assert!(h.released.load(Ordering::SeqCst));
        assert!(!h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn before_hook_exit_fails_and_shuts_down() {
        let cfg = Cfg {
            before: HookResult::Failed,
            connect_client: false,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(matches!(state, LeaseState::Failed(_)));
        assert!(!h
            .reported
            .lock()
            .unwrap()
            .contains(&ExporterStatus::LeaseReady));
        assert!(h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn after_hook_exit_fails_and_shuts_down() {
        let cfg = Cfg {
            after: HookResult::Failed,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(matches!(state, LeaseState::Failed(_)));
        let reported = h.reported.lock().unwrap();
        assert!(reported.contains(&ExporterStatus::LeaseReady));
        assert!(reported.contains(&ExporterStatus::AfterLeaseHook));
        assert!(h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn end_during_before_hook_flashes_lease_ready() {
        let cfg = Cfg {
            connect_client: false,
            end_during_before: true,
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        assert!(matches!(state, LeaseState::Done(_)));
        let reported = h.reported.lock().unwrap();
        assert_eq!(reported[0], ExporterStatus::BeforeLeaseHook);
        // It still flashed LEASE_READY before ending (the #569 grace), even though the lease was
        // already gone when the hook finished.
        assert!(reported.contains(&ExporterStatus::LeaseReady));
        assert!(
            !h.released.load(Ordering::SeqCst),
            "controller-ended: no request_release"
        );
    }

    #[tokio::test]
    async fn dd7_rejected_lease_ready_does_not_serve() {
        let cfg = Cfg {
            connect_client: false,
            reject: Some(ExporterStatus::LeaseReady),
            ..Cfg::default()
        };
        let (state, h) = run(cfg).await;
        // A rejected LEASE_READY must not serve: the lease fails instead.
        assert!(matches!(state, LeaseState::Failed(_)));
        assert!(!h
            .reported
            .lock()
            .unwrap()
            .contains(&ExporterStatus::LeaseReady));
        assert!(h.shutdown.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn per_signal_outcome_is_observable() {
        // A signal's sender can await its Outcome via the envelope reply.
        let (_m, _h, tx, mut mailbox) = mock(Cfg::default());
        let state = LeaseState::initial(ctx());
        // Created ignores a client-connected fact (irrelevant here).
        let (env, rx) = Envelope::with_reply(LeaseSignal::Client(ClientSignal::Connected));
        tx.send(env).unwrap();
        // Drive one step of wait_until_change-style handling inline.
        let env = mailbox.recv().await.unwrap();
        let before = std::mem::discriminant(&state);
        let next = state.apply(env.signal);
        let outcome = if std::mem::discriminant(&next) == before {
            Outcome::Ignored
        } else {
            Outcome::Committed
        };
        ack(env.reply, outcome);
        assert!(matches!(rx.await.unwrap(), Outcome::Ignored));
    }
}
