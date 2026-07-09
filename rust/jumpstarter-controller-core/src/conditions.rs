//! Status-condition helpers, porting `k8s.io/apimachinery/pkg/api/meta`
//! `SetStatusCondition` / `IsStatusConditionTrue` **exactly** (apimachinery
//! v0.33.0, `pkg/api/meta/conditions.go`).
//!
//! The controller relies on the precise `lastTransitionTime` semantics of
//! `meta.SetStatusCondition`, which are observable through `kubectl` and the
//! e2e suites:
//!
//!   * a brand-new condition is appended with `lastTransitionTime = now`
//!     (unless the caller already set a non-zero time);
//!   * an existing condition whose **status is unchanged** keeps its original
//!     `lastTransitionTime` — only `reason`, `message` and
//!     `observedGeneration` are refreshed in place;
//!   * an existing condition whose **status flips** gets a fresh
//!     `lastTransitionTime = now`.
//!
//! The condition is upserted keyed by `type` (patch-merge key), matching the
//! `patchStrategy:"merge" patchMergeKey:"type"` tags on the Go status structs.

use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, Time};
use k8s_openapi::jiff::Timestamp;

/// `metav1.ConditionTrue`.
pub const CONDITION_TRUE: &str = "True";
/// `metav1.ConditionFalse`.
pub const CONDITION_FALSE: &str = "False";

/// Unix-seconds value of Go's zero `time.Time{}` (`0001-01-01T00:00:00Z`),
/// which is what `metav1.Time.IsZero()` tests for. Used as the sentinel for
/// "the caller did not set `lastTransitionTime`", mirroring a Go
/// `metav1.Condition{...}` struct literal that omits the field.
const GO_ZERO_UNIX_SECS: i64 = -62_135_596_800;

/// The Go zero `metav1.Time` (`0001-01-01T00:00:00Z`) as a k8s-openapi [`Time`].
///
/// [`condition`] stamps this onto its output so [`set_status_condition`] treats
/// the transition time as unset — exactly like the Go controllers, which build
/// `metav1.Condition{Type, Status, Reason, Message, ObservedGeneration}` and
/// leave `LastTransitionTime` at its zero value.
pub fn go_zero_time() -> Time {
    Time(Timestamp::from_second(GO_ZERO_UNIX_SECS).expect("go zero time is within jiff range"))
}

/// Whether `time` equals Go's zero `time.Time{}` — the port of
/// `metav1.Time.IsZero()` as used by `meta.SetStatusCondition` (and by the
/// exporter reconciler's `LastSeen.IsZero()` guard).
pub fn is_zero_time(time: &Time) -> bool {
    time.0.as_second() == GO_ZERO_UNIX_SECS && time.0.subsec_nanosecond() == 0
}

/// Build a [`Condition`] the way the Go reconcilers do: an explicit
/// `type`/`status`/`reason`/`message`/`observedGeneration`, with
/// `lastTransitionTime` left unset (Go zero) so [`set_status_condition`] fills
/// it in.
///
/// `status` maps `true -> "True"`, `false -> "False"` (`metav1.ConditionTrue` /
/// `metav1.ConditionFalse`). `observed_generation == 0` is stored as `None`,
/// mirroring the `omitempty` on the non-pointer Go `int64` field so a condition
/// round-tripped through the apiserver compares equal.
pub fn condition(
    type_: &str,
    status: bool,
    observed_generation: i64,
    reason: &str,
    message: &str,
) -> Condition {
    Condition {
        last_transition_time: go_zero_time(),
        message: message.to_string(),
        observed_generation: (observed_generation != 0).then_some(observed_generation),
        reason: reason.to_string(),
        status: if status {
            CONDITION_TRUE
        } else {
            CONDITION_FALSE
        }
        .to_string(),
        type_: type_.to_string(),
    }
}

/// Port of `meta.SetStatusCondition` (apimachinery `conditions.go`), returning
/// whether `conditions` changed. `now` is injected so the reconcilers control
/// the clock and the transition-time rules are table-testable.
///
/// Byte-for-byte behavioral port:
///
///   1. type not present ⇒ append; if the new condition's `lastTransitionTime`
///      is zero, set it to `now`.
///   2. type present, `status` differs ⇒ update `status`; set
///      `lastTransitionTime` to the new condition's value if non-zero else
///      `now`; `changed = true`.
///   3. `reason` / `message` / `observedGeneration` are compared and copied
///      independently, each flipping `changed`.
pub fn set_status_condition(
    conditions: &mut Vec<Condition>,
    new_condition: Condition,
    now: Timestamp,
) -> bool {
    let Some(existing) = conditions
        .iter_mut()
        .find(|c| c.type_ == new_condition.type_)
    else {
        let mut appended = new_condition;
        if is_zero_time(&appended.last_transition_time) {
            appended.last_transition_time = Time(now);
        }
        conditions.push(appended);
        return true;
    };

    let mut changed = false;

    if existing.status != new_condition.status {
        existing.status = new_condition.status.clone();
        existing.last_transition_time = if is_zero_time(&new_condition.last_transition_time) {
            Time(now)
        } else {
            new_condition.last_transition_time.clone()
        };
        changed = true;
    }

    if existing.reason != new_condition.reason {
        existing.reason = new_condition.reason.clone();
        changed = true;
    }
    if existing.message != new_condition.message {
        existing.message = new_condition.message.clone();
        changed = true;
    }
    // Go compares non-pointer int64s; `None` (omitempty 0) and `Some(0)` are
    // the same observed generation.
    if existing.observed_generation.unwrap_or(0) != new_condition.observed_generation.unwrap_or(0) {
        existing.observed_generation = new_condition.observed_generation;
        changed = true;
    }

    changed
}

/// Port of `meta.IsStatusConditionTrue`: true when a condition of `type_` is
/// present and its status is `"True"`.
pub fn is_status_condition_true(conditions: &[Condition], type_: &str) -> bool {
    conditions
        .iter()
        .any(|c| c.type_ == type_ && c.status == CONDITION_TRUE)
}

#[cfg(test)]
mod tests {
    use super::*;

    // A fixed, easily-recognizable "now" and a later "now" for flip tests.
    fn t(secs: i64) -> Timestamp {
        Timestamp::from_second(secs).unwrap()
    }

    #[test]
    fn append_stamps_last_transition_time_with_now() {
        let mut conditions = Vec::new();
        let changed = set_status_condition(
            &mut conditions,
            condition("Online", true, 3, "Seen", "up"),
            t(1_000),
        );
        assert!(changed);
        assert_eq!(conditions.len(), 1);
        let c = &conditions[0];
        assert_eq!(c.type_, "Online");
        assert_eq!(c.status, "True");
        assert_eq!(c.reason, "Seen");
        assert_eq!(c.message, "up");
        assert_eq!(c.observed_generation, Some(3));
        assert_eq!(c.last_transition_time.0.as_second(), 1_000);
    }

    #[test]
    fn unchanged_status_preserves_transition_time_but_refreshes_metadata() {
        // Initial append goes through set_status_condition so the transition
        // time is stamped with the first `now`.
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Online", true, 1, "Seen", "up"),
            t(1_000),
        );
        assert_eq!(conditions[0].last_transition_time.0.as_second(), 1_000);

        // same status, new reason/message/observedGeneration, LATER now.
        let changed = set_status_condition(
            &mut conditions,
            condition("Online", true, 5, "StillSeen", "still up"),
            t(9_999),
        );
        assert!(
            changed,
            "reason/message/observedGeneration changes are changes"
        );
        let c = &conditions[0];
        // transition time did NOT move — status did not flip.
        assert_eq!(c.last_transition_time.0.as_second(), 1_000);
        // but reason/message/observedGeneration were refreshed.
        assert_eq!(c.reason, "StillSeen");
        assert_eq!(c.message, "still up");
        assert_eq!(c.observed_generation, Some(5));
    }

    #[test]
    fn identical_condition_is_not_a_change() {
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Online", true, 2, "Seen", "up"),
            t(1_000),
        );
        let changed = set_status_condition(
            &mut conditions,
            condition("Online", true, 2, "Seen", "up"),
            t(2_000),
        );
        assert!(!changed);
        // transition time untouched.
        assert_eq!(conditions[0].last_transition_time.0.as_second(), 1_000);
    }

    #[test]
    fn status_flip_moves_transition_time_to_now() {
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Online", true, 1, "Seen", "up"),
            t(1_000),
        );
        let changed = set_status_condition(
            &mut conditions,
            condition("Online", false, 1, "Seen", "down"),
            t(5_000),
        );
        assert!(changed);
        let c = &conditions[0];
        assert_eq!(c.status, "False");
        assert_eq!(c.last_transition_time.0.as_second(), 5_000);
    }

    #[test]
    fn observed_generation_zero_and_absent_are_equal() {
        // existing has observedGeneration 0 (stored as None), new is also 0.
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Registered", true, 0, "Register", ""),
            t(1_000),
        );
        assert_eq!(conditions[0].observed_generation, None);
        let changed = set_status_condition(
            &mut conditions,
            condition("Registered", true, 0, "Register", ""),
            t(2_000),
        );
        assert!(!changed, "0 vs absent observedGeneration is not a change");
    }

    #[test]
    fn upsert_keyed_by_type_keeps_other_conditions() {
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Online", true, 1, "Seen", "up"),
            t(1_000),
        );
        set_status_condition(
            &mut conditions,
            condition("Registered", true, 1, "Register", ""),
            t(1_000),
        );
        assert_eq!(conditions.len(), 2);
        // updating Online must not touch Registered.
        set_status_condition(
            &mut conditions,
            condition("Online", false, 1, "Seen", "down"),
            t(2_000),
        );
        assert_eq!(conditions.len(), 2);
        assert!(is_status_condition_true(&conditions, "Registered"));
        assert!(!is_status_condition_true(&conditions, "Online"));
    }

    #[test]
    fn caller_provided_non_zero_transition_time_is_honored_on_flip() {
        // Faithful to the (dead-in-this-controller) branch: a non-zero
        // lastTransitionTime on the new condition wins over `now`.
        let mut conditions = Vec::new();
        set_status_condition(
            &mut conditions,
            condition("Online", true, 1, "Seen", "up"),
            t(1_000),
        );
        let mut flip = condition("Online", false, 1, "Seen", "down");
        flip.last_transition_time = Time(t(42));
        set_status_condition(&mut conditions, flip, t(5_000));
        assert_eq!(conditions[0].last_transition_time.0.as_second(), 42);
    }
}
