//! The exporter lease lifecycle as a **typestate FSM that drives progress** (JEP-0012).
//!
//! This replaces the role of the old passive `LeaseLifecycle` validator: the machine *is* the
//! source of truth. Two layers, deliberately separated:
//!
//! - **Typed transition methods** (`begin_setup`, `enter_before_lease`, `request_end`, …) are
//!   the only way to change state. Each consumes `self` and returns the *concrete* successor
//!   `Lease<ToState>`, so the transition graph is encoded in the type signatures and an
//!   illegal transition does not type-check. State structs keep their fields module-private
//!   (sealed construction), so the only way to obtain a non-initial state is a legal
//!   transition.
//! - **`LeaseSignal`** is the mailbox payload: external *facts*, grouped by origin
//!   (controller / client / hook / system), never a target state. Each typed state's
//!   `apply(signal)` matches every signal exhaustively and routes the relevant ones to its
//!   transition methods; an irrelevant signal is an explicit no-op. Because no signal can name
//!   a destination, the orchestration layer cannot express an incorrect imperative transition.
//!
//! Note on the carrier: the FSM core's `jumpstarter_fsm::Handle` cannot carry our
//! transition methods across crates (the orphan rule forbids inherent impls on a foreign
//! type), so `Lease<S>` is a thin crate-local carrier. We still use the core's `Fsm`/`Live`
//! traits and `#[derive(StateMachine)]`, and the mailbox lives in the core too.
//!
//! # Compile-time guarantees (the negative space)
//!
//! The transition graph is encoded in the type signatures, so an illegal transition is a
//! *compile* error, not a runtime one. The valid happy path compiles:
//!
//! ```
//! use jumpstarter_exporter::lease_fsm::*;
//! use jumpstarter_fsm::Fsm;
//! let ctx = LeaseContext { lease_name: "x".into(), client_name: "c".into(), config: LeaseConfig::default() };
//! let s = Lease::<Created>::new(ctx).begin_setup().enter_before_lease();
//! let s: LeaseState = s.into();
//! let s = s.apply(LeaseSignal::Hook(HookSignal::BeforeDone(HookResult::Ok)));
//! assert!(matches!(s, LeaseState::Ready(_)));
//! ```
//!
//! These `compile_fail` doctests lock the negative space (the trybuild role, without
//! version-brittle stderr). `Lease<Starting>` has no `begin_release` — skipping straight to
//! release does not compile:
//!
//! ```compile_fail
//! use jumpstarter_exporter::lease_fsm::*;
//! let ctx = LeaseContext { lease_name: "x".into(), client_name: "c".into(), config: LeaseConfig::default() };
//! let _ = Lease::<Created>::new(ctx).begin_setup().begin_release();
//! ```
//!
//! A terminal `Lease<Failed>` is not `Live`, so it cannot be failed again:
//!
//! ```compile_fail
//! use jumpstarter_exporter::lease_fsm::*;
//! let ctx = LeaseContext { lease_name: "x".into(), client_name: "c".into(), config: LeaseConfig::default() };
//! let f = Lease::<Created>::new(ctx).fail(LeaseFailureReason::Internal);
//! let _ = f.fail(LeaseFailureReason::Internal);
//! ```
//!
//! A moved state cannot be reused — the typestate is linear (consumed on transition):
//!
//! ```compile_fail
//! use jumpstarter_exporter::lease_fsm::*;
//! let ctx = LeaseContext { lease_name: "x".into(), client_name: "c".into(), config: LeaseConfig::default() };
//! let s = Lease::<Created>::new(ctx).begin_setup();
//! let _a = s.enter_before_lease();
//! let _b = s.mark_ready();
//! ```

use jumpstarter_fsm::{Fsm, Live, StateMachine};
use jumpstarter_protocol::v1::ExporterStatus;

// --- end / failure reasons + hook result fact ---

/// Why a lease is ending — carried into `Ending` and read by `should_run_after_lease`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LeaseEndReason {
    /// The controller marked the lease `leased=false`.
    Controller,
    /// The client called `EndSession`.
    EndSession,
    /// A `beforeLease` hook returned `on_failure: endLease` — end without serving.
    BeforeHookEndLease,
    /// A graceful shutdown signal (SIGINT/SIGTERM).
    Shutdown,
}

/// A terminal local failure in lifecycle orchestration; selects the projected `*_FAILED`/none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LeaseFailureReason {
    BeforeHook,
    AfterHook,
    Internal,
}

/// Outcome of a hook subprocess, observed by the effect task and carried as a fact. The
/// effect layer collapses success/warn into `Ok` and the `on_failure: exit`/timeout/non-zero
/// cases into `Failed`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HookResult {
    /// Hook succeeded (possibly with a non-fatal warning) — proceed.
    Ok,
    /// `on_failure: endLease` — end the lease without serving (before-hook only).
    EndLease,
    /// `on_failure: exit` / non-zero exit / timeout treated as fatal — fail the lease.
    Failed,
    /// The hook effect task panicked.
    Panicked,
}

// --- static config + carried context ---

/// Static configuration for a lease's lifecycle.
#[derive(Clone, Debug, Default)]
pub struct LeaseConfig {
    /// Run the after-lease hook even when no client ever connected.
    pub always_run_after_lease: bool,
}

/// The lease object carried through every transition: identity + config.
#[derive(Clone, Debug)]
pub struct LeaseContext {
    pub lease_name: String,
    pub client_name: String,
    pub config: LeaseConfig,
}

// --- signals: external FACTS, grouped by origin ---

/// The mailbox payload: an observation of the world, never a transition command.
#[derive(Clone, Copy, Debug)]
pub enum LeaseSignal {
    Controller(ControllerSignal),
    Client(ClientSignal),
    Hook(HookSignal),
    System(SystemSignal),
}

/// Facts originating at the controller (the `Status` stream).
#[derive(Clone, Copy, Debug)]
pub enum ControllerSignal {
    /// The controller set `leased=false`.
    Ended,
}

/// Facts originating at the leasing client.
#[derive(Clone, Copy, Debug)]
pub enum ClientSignal {
    /// First client connection accepted on `Listen`.
    Connected,
    /// The client called `EndSession`.
    EndSession,
}

/// Facts originating at a hook subprocess task.
#[derive(Clone, Copy, Debug)]
pub enum HookSignal {
    BeforeDone(HookResult),
    AfterDone(HookResult),
}

/// Facts originating at the host process / OS.
#[derive(Clone, Copy, Debug)]
pub enum SystemSignal {
    /// SIGINT/SIGTERM graceful-shutdown request.
    ShutdownRequested,
    /// A spawned effect task panicked.
    EffectPanicked,
}

// Each origin lifts into `LeaseSignal`, so a `SignalSink<Origin, LeaseSignal>` enforces that
// an effect can only emit facts from its own origin (a hook task cannot post a controller
// signal).
impl From<ControllerSignal> for LeaseSignal {
    fn from(s: ControllerSignal) -> Self {
        LeaseSignal::Controller(s)
    }
}
impl From<ClientSignal> for LeaseSignal {
    fn from(s: ClientSignal) -> Self {
        LeaseSignal::Client(s)
    }
}
impl From<HookSignal> for LeaseSignal {
    fn from(s: HookSignal) -> Self {
        LeaseSignal::Hook(s)
    }
}
impl From<SystemSignal> for LeaseSignal {
    fn from(s: SystemSignal) -> Self {
        LeaseSignal::System(s)
    }
}

// --- typed states: sealed (module-private fields) ---

#[derive(Clone)]
pub struct Created;
#[derive(Clone)]
pub struct Starting;
#[derive(Clone)]
pub struct BeforeLease {
    end_reason: Option<LeaseEndReason>,
    client_connected: bool,
}
#[derive(Clone)]
pub struct Ready {
    client_connected: bool,
    /// Set when the lease ended *during* a slow `beforeLease`: we still flash `LEASE_READY`
    /// briefly (so a client polling `GetStatus` doesn't race to `AVAILABLE`) before ending.
    deferred_end: Option<LeaseEndReason>,
}
#[derive(Clone)]
pub struct Ending {
    reason: LeaseEndReason,
    client_connected: bool,
}
#[derive(Clone)]
pub struct AfterLease {
    reason: LeaseEndReason,
}
#[derive(Clone)]
pub struct Releasing {
    reason: LeaseEndReason,
}
#[derive(Clone)]
pub struct Done;
#[derive(Clone)]
pub struct Failed {
    reason: LeaseFailureReason,
}

/// The lease typestate carrier: the lease context plus the current typed state. Crate-local
/// (see the module note) so transitions can be inherent methods.
#[derive(Clone)]
pub struct Lease<S> {
    ctx: LeaseContext,
    state: S,
}

impl<S> Lease<S> {
    /// Move to the next typed state, carrying the context forward (concrete return).
    fn into_state<T>(self, next: T) -> Lease<T> {
        Lease {
            ctx: self.ctx,
            state: next,
        }
    }

    /// The carried lease context — read it in any transition that needs lease-global state.
    pub fn ctx(&self) -> &LeaseContext {
        &self.ctx
    }

    fn state(&self) -> &S {
        &self.state
    }
}

/// `fail` from any live (non-terminal) state — one method, gated by the `Live` marker so a
/// terminal state cannot be failed (`done.fail(..)` does not compile).
impl<S: Live> Lease<S> {
    pub fn fail(self, reason: LeaseFailureReason) -> Lease<Failed> {
        self.into_state(Failed { reason })
    }
}

impl Lease<Failed> {
    /// The failure reason — selects the projected `*_HOOK_FAILED` / none.
    pub fn reason(&self) -> LeaseFailureReason {
        self.state().reason
    }
}

// --- per-state transitions + exhaustive `apply` ---

impl Lease<Created> {
    /// Start a lease carrying its context (metadata + config).
    pub fn new(ctx: LeaseContext) -> Self {
        Lease {
            ctx,
            state: Created,
        }
    }

    /// Internal progression (decided by the runner): begin session setup.
    pub fn begin_setup(self) -> Lease<Starting> {
        self.into_state(Starting)
    }

    fn request_end(self, reason: LeaseEndReason) -> Lease<Ending> {
        self.into_state(Ending {
            reason,
            client_connected: false,
        })
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::Controller(ControllerSignal::Ended) => {
                self.request_end(LeaseEndReason::Controller).into()
            }
            LeaseSignal::Client(ClientSignal::EndSession) => {
                self.request_end(LeaseEndReason::EndSession).into()
            }
            LeaseSignal::System(SystemSignal::ShutdownRequested) => {
                self.request_end(LeaseEndReason::Shutdown).into()
            }
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
            LeaseSignal::Client(ClientSignal::Connected) | LeaseSignal::Hook(_) => self.into(),
        }
    }
}

impl Lease<Starting> {
    /// Internal progression: a `beforeLease` hook is configured.
    pub fn enter_before_lease(self) -> Lease<BeforeLease> {
        self.into_state(BeforeLease {
            end_reason: None,
            client_connected: false,
        })
    }

    /// Internal progression: no `beforeLease` hook — go straight to `Ready`.
    pub fn mark_ready(self) -> Lease<Ready> {
        self.into_state(Ready {
            client_connected: false,
            deferred_end: None,
        })
    }

    fn request_end(self, reason: LeaseEndReason) -> Lease<Ending> {
        self.into_state(Ending {
            reason,
            client_connected: false,
        })
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::Controller(ControllerSignal::Ended) => {
                self.request_end(LeaseEndReason::Controller).into()
            }
            LeaseSignal::Client(ClientSignal::EndSession) => {
                self.request_end(LeaseEndReason::EndSession).into()
            }
            LeaseSignal::System(SystemSignal::ShutdownRequested) => {
                self.request_end(LeaseEndReason::Shutdown).into()
            }
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
            LeaseSignal::Client(ClientSignal::Connected) | LeaseSignal::Hook(_) => self.into(),
        }
    }
}

impl Lease<BeforeLease> {
    fn mark_client_connected(self) -> Lease<BeforeLease> {
        let end_reason = self.state().end_reason;
        self.into_state(BeforeLease {
            end_reason,
            client_connected: true,
        })
    }

    /// Deferred: an end requested while the hook runs is recorded (first wins) and acted on at
    /// completion, so we stay in `BeforeLease` for now (the hook is never cancelled).
    fn record_end(self, reason: LeaseEndReason) -> Lease<BeforeLease> {
        let end_reason = self.state().end_reason.or(Some(reason));
        let client_connected = self.state().client_connected;
        self.into_state(BeforeLease {
            end_reason,
            client_connected,
        })
    }

    /// Hook completed successfully: go `Ready`, carrying any end that was deferred during the
    /// hook so the runner can flash `LEASE_READY` briefly before ending (the #569 grace).
    fn before_hook_completed(self) -> LeaseState {
        let connected = self.state().client_connected;
        let deferred_end = self.state().end_reason;
        self.into_state(Ready {
            client_connected: connected,
            deferred_end,
        })
        .into()
    }

    fn before_hook_done(self, result: HookResult) -> LeaseState {
        match result {
            HookResult::Ok => self.before_hook_completed(),
            HookResult::EndLease => {
                let connected = self.state().client_connected;
                self.into_state(Ending {
                    reason: LeaseEndReason::BeforeHookEndLease,
                    client_connected: connected,
                })
                .into()
            }
            HookResult::Failed | HookResult::Panicked => {
                self.fail(LeaseFailureReason::BeforeHook).into()
            }
        }
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::Client(ClientSignal::Connected) => self.mark_client_connected().into(),
            LeaseSignal::Controller(ControllerSignal::Ended) => {
                self.record_end(LeaseEndReason::Controller).into()
            }
            LeaseSignal::Client(ClientSignal::EndSession) => {
                self.record_end(LeaseEndReason::EndSession).into()
            }
            LeaseSignal::System(SystemSignal::ShutdownRequested) => {
                self.record_end(LeaseEndReason::Shutdown).into()
            }
            LeaseSignal::Hook(HookSignal::BeforeDone(r)) => self.before_hook_done(r),
            LeaseSignal::Hook(HookSignal::AfterDone(_)) => self.into(),
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
        }
    }
}

impl Lease<Ready> {
    fn mark_client_connected(self) -> Lease<Ready> {
        let deferred_end = self.state().deferred_end;
        self.into_state(Ready {
            client_connected: true,
            deferred_end,
        })
    }

    fn request_end(self, reason: LeaseEndReason) -> Lease<Ending> {
        let client_connected = self.state().client_connected;
        self.into_state(Ending {
            reason,
            client_connected,
        })
    }

    /// The end deferred during a slow `beforeLease`, if any. The runner holds `LEASE_READY`
    /// briefly, then calls [`Self::end_deferred`].
    pub fn deferred_end(&self) -> Option<LeaseEndReason> {
        self.state().deferred_end
    }

    /// Resolve a deferred end into `Ending` (after the `LEASE_READY` grace).
    pub fn end_deferred(self) -> Lease<Ending> {
        let reason = self
            .state()
            .deferred_end
            .unwrap_or(LeaseEndReason::Controller);
        let client_connected = self.state().client_connected;
        self.into_state(Ending {
            reason,
            client_connected,
        })
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::Client(ClientSignal::Connected) => self.mark_client_connected().into(),
            LeaseSignal::Controller(ControllerSignal::Ended) => {
                self.request_end(LeaseEndReason::Controller).into()
            }
            LeaseSignal::Client(ClientSignal::EndSession) => {
                self.request_end(LeaseEndReason::EndSession).into()
            }
            LeaseSignal::System(SystemSignal::ShutdownRequested) => {
                self.request_end(LeaseEndReason::Shutdown).into()
            }
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
            LeaseSignal::Hook(_) => self.into(),
        }
    }
}

impl Lease<Ending> {
    /// The after-lease hook runs if a client connected, the session was ended explicitly, or
    /// config forces it (today's gate at exporter.rs `has_client || reason == EndSession`).
    pub fn should_run_after_lease(&self) -> bool {
        self.ctx().config.always_run_after_lease
            || self.state().client_connected
            || self.state().reason == LeaseEndReason::EndSession
    }

    /// The end reason — read by the runner to decide whether to request release.
    pub fn reason(&self) -> LeaseEndReason {
        self.state().reason
    }

    /// Internal progression: run the after-lease hook.
    pub fn enter_after_lease(self) -> Lease<AfterLease> {
        let reason = self.state().reason;
        self.into_state(AfterLease { reason })
    }

    /// Internal progression: skip the after-lease hook, go straight to release.
    pub fn begin_release(self) -> Lease<Releasing> {
        let reason = self.state().reason;
        self.into_state(Releasing { reason })
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        // `Ending` progresses internally (the runner decides after-lease vs release); a panic
        // still fails it, everything else is an explicit no-op.
        match signal {
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
            LeaseSignal::Controller(ControllerSignal::Ended)
            | LeaseSignal::Client(ClientSignal::Connected)
            | LeaseSignal::Client(ClientSignal::EndSession)
            | LeaseSignal::Hook(HookSignal::BeforeDone(_))
            | LeaseSignal::Hook(HookSignal::AfterDone(_))
            | LeaseSignal::System(SystemSignal::ShutdownRequested) => self.into(),
        }
    }
}

impl Lease<AfterLease> {
    /// Internal progression: after-lease finished, release.
    pub fn begin_release(self) -> Lease<Releasing> {
        let reason = self.state().reason;
        self.into_state(Releasing { reason })
    }

    fn after_hook_done(self, result: HookResult) -> LeaseState {
        match result {
            HookResult::Ok | HookResult::EndLease => self.begin_release().into(),
            HookResult::Failed | HookResult::Panicked => {
                self.fail(LeaseFailureReason::AfterHook).into()
            }
        }
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::Hook(HookSignal::AfterDone(r)) => self.after_hook_done(r),
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::AfterHook).into()
            }
            LeaseSignal::Controller(ControllerSignal::Ended)
            | LeaseSignal::Client(ClientSignal::Connected)
            | LeaseSignal::Client(ClientSignal::EndSession)
            | LeaseSignal::Hook(HookSignal::BeforeDone(_))
            | LeaseSignal::System(SystemSignal::ShutdownRequested) => self.into(),
        }
    }
}

impl Lease<Releasing> {
    /// The end reason — the runner requests release from the controller unless the controller
    /// itself initiated the end.
    pub fn reason(&self) -> LeaseEndReason {
        self.state().reason
    }

    /// Internal progression: cleanup complete.
    pub fn mark_done(self) -> Lease<Done> {
        self.into_state(Done)
    }

    fn apply(self, signal: LeaseSignal) -> LeaseState {
        match signal {
            LeaseSignal::System(SystemSignal::EffectPanicked) => {
                self.fail(LeaseFailureReason::Internal).into()
            }
            LeaseSignal::Controller(ControllerSignal::Ended)
            | LeaseSignal::Client(ClientSignal::Connected)
            | LeaseSignal::Client(ClientSignal::EndSession)
            | LeaseSignal::Hook(HookSignal::BeforeDone(_))
            | LeaseSignal::Hook(HookSignal::AfterDone(_))
            | LeaseSignal::System(SystemSignal::ShutdownRequested) => self.into(),
        }
    }
}

// Terminal states (`Done`, `Failed`) have no transitions and define no `apply`; the derived
// `Fsm::apply` leaves them unchanged.

/// Type-erased, signal-driven view of the lease. `#[derive(StateMachine)]` generates the
/// `Live` impls, the `From<Lease<S>>` conversions, and the `Fsm` impl (`apply`, `is_terminal`);
/// the signal type is `LeaseSignal`.
#[derive(Clone, StateMachine)]
#[machine(signal = LeaseSignal)]
pub enum LeaseState {
    Created(Lease<Created>),
    Starting(Lease<Starting>),
    BeforeLease(Lease<BeforeLease>),
    Ready(Lease<Ready>),
    Ending(Lease<Ending>),
    AfterLease(Lease<AfterLease>),
    Releasing(Lease<Releasing>),
    #[terminal]
    Done(Lease<Done>),
    #[terminal]
    Failed(Lease<Failed>),
}

impl LeaseState {
    /// Start a lease carrying its context (metadata + config).
    pub fn initial(ctx: LeaseContext) -> Self {
        LeaseState::Created(Lease::<Created>::new(ctx))
    }
}

// --- projection to the wire `ExporterStatus` (DD-2) + the controller's accepted table (DD-6) ---

/// The wire status a state projects onto, if any. `Ending`/`Done`/`Failed(Internal)` are
/// internal-only (no projection). Subsumes the old `LeasePhase::wire_status`.
pub fn project(state: &LeaseState) -> Option<ExporterStatus> {
    use ExporterStatus as S;
    Some(match state {
        LeaseState::Created(_) | LeaseState::Starting(_) => S::Available,
        LeaseState::BeforeLease(_) => S::BeforeLeaseHook,
        LeaseState::Ready(_) => S::LeaseReady,
        LeaseState::Ending(_) => return None,
        LeaseState::AfterLease(_) => S::AfterLeaseHook,
        LeaseState::Releasing(_) => S::Available,
        LeaseState::Done(_) => return None,
        LeaseState::Failed(lease) => match lease.reason() {
            LeaseFailureReason::BeforeHook => S::BeforeLeaseHookFailed,
            LeaseFailureReason::AfterHook => S::AfterLeaseHookFailed,
            LeaseFailureReason::Internal => return None,
        },
    })
}

/// The controller's `ValidExporterTransitions`: the only `ExporterStatus` edges it accepts
/// (DD-6). The FSM's projection must stay inside this table.
pub fn valid_transition(from: ExporterStatus, to: ExporterStatus) -> bool {
    use ExporterStatus::*;
    matches!(
        (from, to),
        (Offline, Available)
            | (Available, BeforeLeaseHook)
            | (Available, LeaseReady) // no-hook exporters skip BEFORE_LEASE_HOOK
            | (Available, Offline)
            | (BeforeLeaseHook, LeaseReady)
            | (BeforeLeaseHook, BeforeLeaseHookFailed)
            | (LeaseReady, AfterLeaseHook)
            | (AfterLeaseHook, Available)
            | (AfterLeaseHook, AfterLeaseHookFailed)
            | (BeforeLeaseHookFailed, Offline)
            | (AfterLeaseHookFailed, Offline)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> LeaseContext {
        LeaseContext {
            lease_name: "demo".into(),
            client_name: "client".into(),
            config: LeaseConfig::default(),
        }
    }

    #[test]
    fn happy_path_with_hooks_drives_and_projects() {
        // Created -> Starting -> BeforeLease (typed internal progressions).
        let s: LeaseState = Lease::<Created>::new(ctx())
            .begin_setup()
            .enter_before_lease()
            .into();
        assert_eq!(project(&s), Some(ExporterStatus::BeforeLeaseHook));

        // Client connects, before-hook completes -> Ready.
        let s = s.apply(LeaseSignal::Client(ClientSignal::Connected));
        let s = s.apply(LeaseSignal::Hook(HookSignal::BeforeDone(HookResult::Ok)));
        assert!(matches!(s, LeaseState::Ready(_)));
        assert_eq!(project(&s), Some(ExporterStatus::LeaseReady));

        // Controller ends -> Ending (a client connected, so after-lease should run).
        let s = s.apply(LeaseSignal::Controller(ControllerSignal::Ended));
        let ending = match s {
            LeaseState::Ending(e) => e,
            _ => panic!("expected Ending"),
        };
        assert!(ending.should_run_after_lease());

        // After-lease hook -> Releasing -> Done.
        let s: LeaseState = ending.enter_after_lease().into();
        assert_eq!(project(&s), Some(ExporterStatus::AfterLeaseHook));
        let s = s.apply(LeaseSignal::Hook(HookSignal::AfterDone(HookResult::Ok)));
        let releasing = match s {
            LeaseState::Releasing(r) => r,
            _ => panic!("expected Releasing"),
        };
        let s: LeaseState = releasing.mark_done().into();
        assert!(matches!(s, LeaseState::Done(_)));
        assert!(s.is_terminal());
        assert_eq!(project(&s), None);
    }

    #[test]
    fn deferred_end_during_before_hook_holds_ready_then_ends() {
        let s: LeaseState = Lease::<Created>::new(ctx())
            .begin_setup()
            .enter_before_lease()
            .into();
        // End arrives mid-hook: recorded, stays in BeforeLease (the hook is never cancelled).
        let s = s.apply(LeaseSignal::Controller(ControllerSignal::Ended));
        assert!(
            matches!(s, LeaseState::BeforeLease(_)),
            "deferred, stays put"
        );
        // On completion it routes to Ready carrying the deferred end (so the runner can flash
        // LEASE_READY briefly), never skipping straight past it.
        let s = s.apply(LeaseSignal::Hook(HookSignal::BeforeDone(HookResult::Ok)));
        let ready = match s {
            LeaseState::Ready(r) => r,
            _ => panic!("expected Ready"),
        };
        assert_eq!(ready.deferred_end(), Some(LeaseEndReason::Controller));
        // The runner resolves the deferred end into Ending after the grace.
        let s: LeaseState = ready.end_deferred().into();
        assert!(matches!(s, LeaseState::Ending(_)));
    }

    #[test]
    fn before_hook_failure_fails_and_projects() {
        let s: LeaseState = Lease::<Created>::new(ctx())
            .begin_setup()
            .enter_before_lease()
            .into();
        let s = s.apply(LeaseSignal::Hook(HookSignal::BeforeDone(
            HookResult::Failed,
        )));
        assert!(matches!(s, LeaseState::Failed(_)));
        assert_eq!(project(&s), Some(ExporterStatus::BeforeLeaseHookFailed));
    }

    #[test]
    fn no_before_hook_shortcut_projects_available_to_lease_ready() {
        let s: LeaseState = Lease::<Created>::new(ctx())
            .begin_setup()
            .mark_ready()
            .into();
        assert_eq!(project(&s), Some(ExporterStatus::LeaseReady));
        assert!(valid_transition(
            ExporterStatus::Available,
            ExporterStatus::LeaseReady
        ));
    }

    #[test]
    fn config_can_force_after_lease_hook() {
        let forced = LeaseContext {
            lease_name: "forced".into(),
            client_name: "c".into(),
            config: LeaseConfig {
                always_run_after_lease: true,
            },
        };
        // No client connected, ended by the controller — without the flag the hook is skipped.
        let s: LeaseState = Lease::<Created>::new(forced)
            .begin_setup()
            .mark_ready()
            .into();
        let s = s.apply(LeaseSignal::Controller(ControllerSignal::Ended));
        match s {
            LeaseState::Ending(e) => assert!(e.should_run_after_lease(), "config forces the hook"),
            _ => panic!("expected Ending"),
        }
    }

    #[test]
    fn out_of_order_signal_is_ignored() {
        // A hook-done fact is irrelevant in Created — an explicit no-op, not a transition.
        let s = LeaseState::initial(ctx());
        let s = s.apply(LeaseSignal::Hook(HookSignal::AfterDone(HookResult::Ok)));
        assert!(matches!(s, LeaseState::Created(_)), "unchanged");
    }

    /// DD-7: a status report rejected by the controller must NOT advance the FSM. The FSM is
    /// pure and `Clone`, so the orchestrator peeks the candidate, reports, and commits only on
    /// accept.
    #[test]
    fn rejected_report_does_not_advance() {
        let s: LeaseState = Lease::<Created>::new(ctx())
            .begin_setup()
            .enter_before_lease()
            .into();
        let s = s.apply(LeaseSignal::Client(ClientSignal::Connected));
        assert!(matches!(s, LeaseState::BeforeLease(_)));

        // Peek the candidate that BeforeDone(Ok) would produce; it projects LEASE_READY.
        let candidate = s
            .clone()
            .apply(LeaseSignal::Hook(HookSignal::BeforeDone(HookResult::Ok)));
        assert_eq!(project(&candidate), Some(ExporterStatus::LeaseReady));

        // The controller rejects LEASE_READY → keep `s`, do not adopt the candidate.
        let report_accepts = |st: ExporterStatus| st != ExporterStatus::LeaseReady;
        let s = match project(&candidate) {
            Some(st) if !report_accepts(st) => s,
            _ => candidate,
        };
        assert!(
            matches!(s, LeaseState::BeforeLease(_)),
            "a rejected LEASE_READY report must not advance the FSM"
        );
    }
}

/// Structural tests ported from the old `fsm.rs` table tests, adjusted for this model:
/// the agreed transition table (tightened so `Ending` always reaches `Done` via `Releasing`),
/// terminal absorption, the wire-status projection, and the DD-6 projection-validity sweep.
#[cfg(test)]
mod table_tests {
    use super::*;

    fn ctx() -> LeaseContext {
        LeaseContext {
            lease_name: "demo".into(),
            client_name: "client".into(),
            config: LeaseConfig::default(),
        }
    }

    fn vn(s: &LeaseState) -> &'static str {
        match s {
            LeaseState::Created(_) => "Created",
            LeaseState::Starting(_) => "Starting",
            LeaseState::BeforeLease(_) => "BeforeLease",
            LeaseState::Ready(_) => "Ready",
            LeaseState::Ending(_) => "Ending",
            LeaseState::AfterLease(_) => "AfterLease",
            LeaseState::Releasing(_) => "Releasing",
            LeaseState::Done(_) => "Done",
            LeaseState::Failed(_) => "Failed",
        }
    }

    // Fresh typed handles for exercising each edge (every call rebuilds from scratch).
    fn created() -> Lease<Created> {
        Lease::<Created>::new(ctx())
    }
    fn starting() -> Lease<Starting> {
        created().begin_setup()
    }
    fn before() -> Lease<BeforeLease> {
        starting().enter_before_lease()
    }
    fn ready() -> Lease<Ready> {
        starting().mark_ready()
    }
    fn ending() -> Lease<Ending> {
        match LeaseState::Ready(ready()).apply(ctrl_ended()) {
            LeaseState::Ending(e) => e,
            _ => unreachable!(),
        }
    }
    fn after() -> Lease<AfterLease> {
        ending().enter_after_lease()
    }
    fn releasing() -> Lease<Releasing> {
        ending().begin_release()
    }
    fn done_state() -> LeaseState {
        releasing().mark_done().into()
    }

    fn ctrl_ended() -> LeaseSignal {
        LeaseSignal::Controller(ControllerSignal::Ended)
    }
    fn panic_sig() -> LeaseSignal {
        LeaseSignal::System(SystemSignal::EffectPanicked)
    }
    fn before_done(r: HookResult) -> LeaseSignal {
        LeaseSignal::Hook(HookSignal::BeforeDone(r))
    }
    fn after_done(r: HookResult) -> LeaseSignal {
        LeaseSignal::Hook(HookSignal::AfterDone(r))
    }

    type Edges = std::collections::BTreeSet<(&'static str, &'static str)>;
    fn add(edges: &mut Edges, from: &'static str, to: &LeaseState) {
        let t = vn(to);
        if t != from {
            edges.insert((from, t));
        }
    }

    #[test]
    fn reachable_edges_match_agreed_tightened_table() {
        let mut edges = Edges::new();

        // Internal (typed) progressions decided by the runner.
        add(&mut edges, "Created", &created().begin_setup().into());
        add(
            &mut edges,
            "Starting",
            &starting().enter_before_lease().into(),
        );
        add(&mut edges, "Starting", &starting().mark_ready().into());
        add(&mut edges, "Ending", &ending().enter_after_lease().into());
        add(&mut edges, "Ending", &ending().begin_release().into());
        add(&mut edges, "AfterLease", &after().begin_release().into());
        add(&mut edges, "Releasing", &releasing().mark_done().into());

        // `fail` from each live state.
        add(
            &mut edges,
            "Created",
            &created().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "Starting",
            &starting().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "BeforeLease",
            &before().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "Ready",
            &ready().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "Ending",
            &ending().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "AfterLease",
            &after().fail(LeaseFailureReason::Internal).into(),
        );
        add(
            &mut edges,
            "Releasing",
            &releasing().fail(LeaseFailureReason::Internal).into(),
        );

        // Signal-driven cross-variant edges.
        add(
            &mut edges,
            "Created",
            &LeaseState::Created(created()).apply(ctrl_ended()),
        );
        add(
            &mut edges,
            "Starting",
            &LeaseState::Starting(starting()).apply(ctrl_ended()),
        );
        add(
            &mut edges,
            "Ready",
            &LeaseState::Ready(ready()).apply(ctrl_ended()),
        );
        add(
            &mut edges,
            "BeforeLease",
            &LeaseState::BeforeLease(before()).apply(before_done(HookResult::Ok)),
        ); // -> Ready
        add(
            &mut edges,
            "BeforeLease",
            // an `endLease` before-hook ends the lease without serving -> Ending.
            &LeaseState::BeforeLease(before()).apply(before_done(HookResult::EndLease)),
        );
        add(
            &mut edges,
            "BeforeLease",
            &LeaseState::BeforeLease(before()).apply(before_done(HookResult::Failed)),
        ); // -> Failed
        add(
            &mut edges,
            "AfterLease",
            &LeaseState::AfterLease(after()).apply(after_done(HookResult::Ok)),
        ); // -> Releasing
        add(
            &mut edges,
            "AfterLease",
            &LeaseState::AfterLease(after()).apply(after_done(HookResult::Failed)),
        ); // -> Failed

        let expected: Edges = [
            ("Created", "Starting"),
            ("Created", "Ending"),
            ("Created", "Failed"),
            ("Starting", "BeforeLease"),
            ("Starting", "Ready"),
            ("Starting", "Ending"),
            ("Starting", "Failed"),
            ("BeforeLease", "Ready"),
            ("BeforeLease", "Ending"),
            ("BeforeLease", "Failed"),
            ("Ready", "Ending"),
            ("Ready", "Failed"),
            ("Ending", "AfterLease"),
            ("Ending", "Releasing"),
            ("Ending", "Failed"),
            ("AfterLease", "Releasing"),
            ("AfterLease", "Failed"),
            ("Releasing", "Done"),
            ("Releasing", "Failed"),
        ]
        .into_iter()
        .collect();

        assert_eq!(
            edges, expected,
            "reachable edge set diverged from the agreed table"
        );
    }

    #[test]
    fn terminal_states_absorb_all_signals() {
        let all = [
            ctrl_ended(),
            LeaseSignal::Client(ClientSignal::Connected),
            LeaseSignal::Client(ClientSignal::EndSession),
            before_done(HookResult::Ok),
            after_done(HookResult::Ok),
            LeaseSignal::System(SystemSignal::ShutdownRequested),
            panic_sig(),
        ];
        let done = done_state();
        assert!(matches!(done, LeaseState::Done(_)));
        for s in all {
            assert!(matches!(done.clone().apply(s), LeaseState::Done(_)));
        }
        let failed: LeaseState = created().fail(LeaseFailureReason::Internal).into();
        for s in all {
            assert!(matches!(failed.clone().apply(s), LeaseState::Failed(_)));
        }
    }

    #[test]
    fn projection_matches_every_state() {
        use ExporterStatus as S;
        assert_eq!(project(&LeaseState::Created(created())), Some(S::Available));
        assert_eq!(
            project(&LeaseState::Starting(starting())),
            Some(S::Available)
        );
        assert_eq!(
            project(&LeaseState::BeforeLease(before())),
            Some(S::BeforeLeaseHook)
        );
        assert_eq!(project(&LeaseState::Ready(ready())), Some(S::LeaseReady));
        assert_eq!(project(&LeaseState::Ending(ending())), None);
        assert_eq!(
            project(&LeaseState::AfterLease(after())),
            Some(S::AfterLeaseHook)
        );
        assert_eq!(
            project(&LeaseState::Releasing(releasing())),
            Some(S::Available)
        );
        assert_eq!(project(&done_state()), None);
        let fail_before: LeaseState = before().fail(LeaseFailureReason::BeforeHook).into();
        assert_eq!(project(&fail_before), Some(S::BeforeLeaseHookFailed));
        let fail_after: LeaseState = after().fail(LeaseFailureReason::AfterHook).into();
        assert_eq!(project(&fail_after), Some(S::AfterLeaseHookFailed));
        let fail_internal: LeaseState = created().fail(LeaseFailureReason::Internal).into();
        assert_eq!(project(&fail_internal), None);
    }

    // --- DD-6: every projected transition across every lifecycle path must be a valid
    // ExporterStatus edge the controller accepts. ---

    /// A single lifecycle step: an internal typed progression or an applied signal.
    enum Step {
        Begin,
        EnterBefore,
        MarkReady,
        EnterAfter,
        MarkDone,
        Sig(LeaseSignal),
    }

    fn step(s: LeaseState, st: &Step) -> LeaseState {
        match st {
            Step::Begin => match s {
                LeaseState::Created(c) => c.begin_setup().into(),
                x => x,
            },
            Step::EnterBefore => match s {
                LeaseState::Starting(c) => c.enter_before_lease().into(),
                x => x,
            },
            Step::MarkReady => match s {
                LeaseState::Starting(c) => c.mark_ready().into(),
                x => x,
            },
            Step::EnterAfter => match s {
                LeaseState::Ending(e) => e.enter_after_lease().into(),
                x => x,
            },
            Step::MarkDone => match s {
                LeaseState::Releasing(r) => r.mark_done().into(),
                x => x,
            },
            Step::Sig(sig) => s.apply(*sig),
        }
    }

    /// Drive `steps`, project each state, report only changes, and assert every reported
    /// transition is valid per `valid_transition`. Returns the reported status sequence.
    fn assert_projection_valid(steps: &[Step]) -> Vec<ExporterStatus> {
        let mut state = LeaseState::initial(ctx());
        let mut last = ExporterStatus::Available;
        let mut reported = Vec::new();
        let record = |state: &LeaseState, last: &mut ExporterStatus, reported: &mut Vec<_>| {
            if let Some(status) = project(state) {
                if status != *last {
                    assert!(
                        valid_transition(*last, status),
                        "invalid ExporterStatus transition {:?} -> {:?}",
                        *last,
                        status
                    );
                    *last = status;
                    reported.push(status);
                }
            }
        };
        record(&state, &mut last, &mut reported);
        for st in steps {
            state = step(state, st);
            record(&state, &mut last, &mut reported);
        }
        reported
    }

    #[test]
    fn projection_valid_with_hooks() {
        let reported = assert_projection_valid(&[
            Step::Begin,
            Step::EnterBefore,
            Step::Sig(LeaseSignal::Client(ClientSignal::Connected)),
            Step::Sig(before_done(HookResult::Ok)),
            Step::Sig(ctrl_ended()),
            Step::EnterAfter,
            Step::Sig(after_done(HookResult::Ok)),
            Step::MarkDone,
        ]);
        assert_eq!(
            reported,
            vec![
                ExporterStatus::BeforeLeaseHook,
                ExporterStatus::LeaseReady,
                ExporterStatus::AfterLeaseHook,
                ExporterStatus::Available,
            ]
        );
    }

    #[test]
    fn projection_valid_no_before_hook() {
        let reported = assert_projection_valid(&[
            Step::Begin,
            Step::MarkReady,
            Step::Sig(ctrl_ended()),
            Step::EnterAfter,
            Step::Sig(after_done(HookResult::Ok)),
            Step::MarkDone,
        ]);
        assert_eq!(
            reported,
            vec![
                ExporterStatus::LeaseReady,
                ExporterStatus::AfterLeaseHook,
                ExporterStatus::Available,
            ]
        );
    }

    #[test]
    fn projection_valid_before_hook_failure() {
        let reported = assert_projection_valid(&[
            Step::Begin,
            Step::EnterBefore,
            Step::Sig(before_done(HookResult::Failed)),
        ]);
        assert_eq!(
            reported,
            vec![
                ExporterStatus::BeforeLeaseHook,
                ExporterStatus::BeforeLeaseHookFailed,
            ]
        );
    }

    #[test]
    fn projection_valid_after_hook_failure() {
        let reported = assert_projection_valid(&[
            Step::Begin,
            Step::EnterBefore,
            Step::Sig(before_done(HookResult::Ok)),
            Step::Sig(ctrl_ended()),
            Step::EnterAfter,
            Step::Sig(after_done(HookResult::Failed)),
        ]);
        assert_eq!(
            reported,
            vec![
                ExporterStatus::BeforeLeaseHook,
                ExporterStatus::LeaseReady,
                ExporterStatus::AfterLeaseHook,
                ExporterStatus::AfterLeaseHookFailed,
            ]
        );
    }
}
