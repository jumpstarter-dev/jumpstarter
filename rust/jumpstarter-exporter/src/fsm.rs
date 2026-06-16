//! The exporter lease-lifecycle state machine (spec doc 03; ported from
//! `specs/rust-core/references/impl-jep-0012-lease_lifecycle.py`).
//!
//! The phases form an explicit, compile-time-checked machine: a lease moves
//! `Created → Starting → [BeforeLease] → [Ready] → Ending → [AfterLease] →
//! Releasing → Done`, with `Failed` as the terminal error phase. Hooks run on entry
//! to `BeforeLease`/`AfterLease`, and each non-failure phase projects onto a wire
//! `ExporterStatus` (the status messages themselves live in [`crate::hooks`], which
//! owns the observable status sequence).
//!
//! Modelling this as a self-validating type means an out-of-order transition is a
//! caught error rather than a silent wire-protocol bug — the failure mode that bit
//! increment 1.

use jumpstarter_protocol::v1::ExporterStatus;

/// A phase in a lease's lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LeasePhase {
    Created,
    Starting,
    BeforeLease,
    Ready,
    Ending,
    AfterLease,
    Releasing,
    Done,
    Failed,
}

impl LeasePhase {
    /// Whether `self -> target` is a valid transition (the exact table from the
    /// JEP-0012 prototype, `impl-jep-0012-lease_lifecycle.py:30-44`).
    pub fn can_transition_to(self, target: LeasePhase) -> bool {
        use LeasePhase::*;
        matches!(
            (self, target),
            (Created, Starting)
                | (Created, Failed)
                | (Starting, BeforeLease)
                | (Starting, Ready)
                | (Starting, Ending)
                | (Starting, Failed)
                | (BeforeLease, Ready)
                | (BeforeLease, Ending)
                | (BeforeLease, Failed)
                | (Ready, Ending)
                | (Ready, Failed)
                | (Ending, AfterLease)
                | (Ending, Releasing)
                | (Ending, Done)
                | (Ending, Failed)
                | (AfterLease, Releasing)
                | (AfterLease, Failed)
                | (Releasing, Done)
                | (Releasing, Failed)
        )
    }

    /// The wire status a phase projects onto, if any (spec doc 03 §status
    /// projection). Failure statuses (`*_HOOK_FAILED`, `OFFLINE`) are not phase
    /// projections — they are reported explicitly by the hook orchestration.
    pub fn wire_status(self) -> Option<ExporterStatus> {
        match self {
            LeasePhase::BeforeLease => Some(ExporterStatus::BeforeLeaseHook),
            LeasePhase::Ready => Some(ExporterStatus::LeaseReady),
            LeasePhase::AfterLease => Some(ExporterStatus::AfterLeaseHook),
            LeasePhase::Done => Some(ExporterStatus::Available),
            _ => None,
        }
    }

    /// Whether this is a terminal phase.
    pub fn is_terminal(self) -> bool {
        matches!(self, LeasePhase::Done | LeasePhase::Failed)
    }
}

/// An attempted invalid lifecycle transition.
#[derive(Debug, thiserror::Error)]
#[error("invalid lease transition {from:?} -> {to:?}")]
pub struct InvalidTransition {
    pub from: LeasePhase,
    pub to: LeasePhase,
}

/// A lease lifecycle tracking its current [`LeasePhase`] and enforcing valid
/// transitions.
#[derive(Debug)]
pub struct LeaseLifecycle {
    phase: LeasePhase,
}

impl Default for LeaseLifecycle {
    fn default() -> Self {
        Self::new()
    }
}

impl LeaseLifecycle {
    pub fn new() -> Self {
        Self {
            phase: LeasePhase::Created,
        }
    }

    pub fn phase(&self) -> LeasePhase {
        self.phase
    }

    /// Advance to `target`, returning an error (and leaving the phase unchanged)
    /// for an invalid transition.
    pub fn transition(&mut self, target: LeasePhase) -> Result<(), InvalidTransition> {
        if self.phase.can_transition_to(target) {
            tracing::trace!(from = ?self.phase, to = ?target, "lease lifecycle transition");
            self.phase = target;
            Ok(())
        } else {
            Err(InvalidTransition {
                from: self.phase,
                to: target,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::LeasePhase::*;
    use super::*;

    const ALL: [LeasePhase; 9] = [
        Created,
        Starting,
        BeforeLease,
        Ready,
        Ending,
        AfterLease,
        Releasing,
        Done,
        Failed,
    ];

    /// The full set of valid transitions, mirroring the JEP-0012 prototype.
    fn expected_edges() -> Vec<(LeasePhase, LeasePhase)> {
        vec![
            (Created, Starting),
            (Created, Failed),
            (Starting, BeforeLease),
            (Starting, Ready),
            (Starting, Ending),
            (Starting, Failed),
            (BeforeLease, Ready),
            (BeforeLease, Ending),
            (BeforeLease, Failed),
            (Ready, Ending),
            (Ready, Failed),
            (Ending, AfterLease),
            (Ending, Releasing),
            (Ending, Done),
            (Ending, Failed),
            (AfterLease, Releasing),
            (AfterLease, Failed),
            (Releasing, Done),
            (Releasing, Failed),
        ]
    }

    #[test]
    fn transition_table_matches_prototype_exactly() {
        let allowed: std::collections::HashSet<_> = expected_edges().into_iter().collect();
        for &from in &ALL {
            for &to in &ALL {
                assert_eq!(
                    from.can_transition_to(to),
                    allowed.contains(&(from, to)),
                    "transition {from:?} -> {to:?} disagrees with the expected table",
                );
            }
        }
    }

    #[test]
    fn terminal_phases_have_no_successors() {
        for &to in &ALL {
            assert!(!Done.can_transition_to(to));
            assert!(!Failed.can_transition_to(to));
        }
        assert!(Done.is_terminal() && Failed.is_terminal());
        assert!(!Ready.is_terminal());
    }

    #[test]
    fn wire_status_projection() {
        assert_eq!(
            BeforeLease.wire_status(),
            Some(ExporterStatus::BeforeLeaseHook)
        );
        assert_eq!(Ready.wire_status(), Some(ExporterStatus::LeaseReady));
        assert_eq!(
            AfterLease.wire_status(),
            Some(ExporterStatus::AfterLeaseHook)
        );
        assert_eq!(Done.wire_status(), Some(ExporterStatus::Available));
        for &p in &[Created, Starting, Ending, Releasing, Failed] {
            assert_eq!(p.wire_status(), None);
        }
    }

    #[test]
    fn lifecycle_enforces_valid_path_and_rejects_invalid() {
        let mut lc = LeaseLifecycle::new();
        assert_eq!(lc.phase(), Created);
        // The happy path with a beforeLease + afterLease hook.
        for p in [
            Starting,
            BeforeLease,
            Ready,
            Ending,
            AfterLease,
            Releasing,
            Done,
        ] {
            lc.transition(p).expect("valid transition");
        }
        assert_eq!(lc.phase(), Done);

        // A bad jump is rejected and leaves the phase unchanged.
        let mut lc = LeaseLifecycle::new();
        lc.transition(Starting).unwrap();
        let err = lc.transition(Done).unwrap_err();
        assert_eq!((err.from, err.to), (Starting, Done));
        assert_eq!(lc.phase(), Starting);
    }
}
