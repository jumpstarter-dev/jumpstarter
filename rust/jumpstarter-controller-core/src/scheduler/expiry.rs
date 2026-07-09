//! Port of the expiration arithmetic in `reconcileStatusEnded`
//! (go: lease_controller.go:143-162): given a lease that has already begun
//! (`status.beginTime` set), decide whether it has expired *now* or when to
//! requeue for its future expiry.
//!
//! Only the expiration branch is ported here — the surrounding
//! `unsatisfiable`/`invalid` → ended and `spec.release` → released branches
//! (go: lease_controller.go:133-142) read status conditions and are handled by
//! the kube-facing reconciler.
//!
//! Expiration precedence (go: lease_controller.go:145-154):
//! 1. `spec.endTime`, else
//! 2. `spec.beginTime + spec.duration`, else
//! 3. `status.beginTime + spec.duration`, else
//! 4. the Go zero time (`0001-01-01`).
//!
//! The **zero-time quirk is preserved**: when none of the three inputs is set,
//! `expiration` is the zero time, which is `Before(now)` for any real clock, so
//! the lease expires immediately (go: lease_controller.go:156).

use chrono::{DateTime, Duration as ChronoDuration, Utc};

use jumpstarter_controller_api::go_duration::GoDuration;

/// The outcome of the expiry check.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExpiryDecision {
    /// The lease has expired (or has no computable expiration) and must end now.
    /// go: lease_controller.go:157 `lease.Expire(ctx)`
    Expire,
    /// The lease is still active; requeue at this instant to re-check expiry.
    /// go: lease_controller.go:160 `result.RequeueAfter = expiration.Sub(now)`
    RequeueAt(DateTime<Utc>),
}

/// Port of the expiration decision in `reconcileStatusEnded`
/// (go: lease_controller.go:143-162). `status_begin_time` is the actual
/// acquisition time (`status.beginTime`); Go only reaches this code with it
/// set, so it is passed by value.
///
/// go: lease_controller.go:143-162
pub fn evaluate_expiry(
    spec_begin_time: Option<DateTime<Utc>>,
    spec_end_time: Option<DateTime<Utc>>,
    spec_duration: Option<GoDuration>,
    status_begin_time: DateTime<Utc>,
    now: DateTime<Utc>,
) -> ExpiryDecision {
    // Compute `expiration` following the Go precedence. `None` stands in for
    // the Go zero time, which is always before `now`.
    let expiration: Option<DateTime<Utc>> = if let Some(end) = spec_end_time {
        // Expires at spec.EndTime when specified.
        Some(end)
    } else if let (Some(begin), Some(duration)) = (spec_begin_time, spec_duration) {
        // Scheduled lease: spec.BeginTime + spec.Duration.
        Some(begin + ChronoDuration::nanoseconds(duration.0))
    } else {
        // Immediate lease: actual status.BeginTime + spec.Duration, or — when no
        // duration is set either — the Go zero time (`None`), which is always
        // before `now` and so expires immediately.
        spec_duration.map(|duration| status_begin_time + ChronoDuration::nanoseconds(duration.0))
    };

    match expiration {
        Some(expiration) if expiration >= now => ExpiryDecision::RequeueAt(expiration),
        _ => ExpiryDecision::Expire,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_api::go_duration::SECOND;

    fn t(secs: i64) -> DateTime<Utc> {
        DateTime::<Utc>::from_timestamp(1_700_000_000 + secs, 0).unwrap()
    }

    /// Precedence 1: spec.EndTime wins even when Duration would suggest later.
    // go: lease_controller_test.go:1325-1355 ("expires based on Spec.EndTime")
    #[test]
    fn end_time_takes_precedence_over_duration() {
        // EndTime at +1s, Duration 10s. now = +2s → past EndTime → Expire.
        let decision = evaluate_expiry(None, Some(t(1)), Some(GoDuration(10 * SECOND)), t(0), t(2));
        assert_eq!(decision, ExpiryDecision::Expire);
    }

    /// Precedence 1 (future): EndTime in the future → requeue at EndTime.
    #[test]
    fn end_time_future_requeues_at_end_time() {
        let decision = evaluate_expiry(None, Some(t(10)), Some(GoDuration(SECOND)), t(0), t(2));
        assert_eq!(decision, ExpiryDecision::RequeueAt(t(10)));
    }

    /// Precedence 2: spec.BeginTime + Duration (scheduled lease).
    // go: lease_controller_test.go:1358-1392 ("expires based on BeginTime + Duration")
    #[test]
    fn scheduled_lease_uses_spec_begin_plus_duration() {
        // BeginTime +1s, Duration 1s → expiry at +2s. Status begin differs.
        let decision = evaluate_expiry(
            Some(t(1)),
            None,
            Some(GoDuration(SECOND)),
            t(5), // status begin is ignored when spec.begin is set
            t(3),
        );
        assert_eq!(decision, ExpiryDecision::Expire); // now +3s > +2s
    }

    #[test]
    fn scheduled_lease_future_requeues_at_spec_begin_plus_duration() {
        let decision = evaluate_expiry(Some(t(1)), None, Some(GoDuration(10 * SECOND)), t(5), t(3));
        assert_eq!(decision, ExpiryDecision::RequeueAt(t(11)));
    }

    /// Precedence 3: immediate lease → status.BeginTime + Duration.
    // go: lease_controller_test.go:1394-1428 ("expires based on Status.BeginTime + Duration")
    #[test]
    fn immediate_lease_uses_status_begin_plus_duration() {
        // No spec.begin/end, Duration 1s, status begin at +0 → expiry +1s.
        let decision = evaluate_expiry(None, None, Some(GoDuration(SECOND)), t(0), t(2));
        assert_eq!(decision, ExpiryDecision::Expire);
        let decision = evaluate_expiry(None, None, Some(GoDuration(10 * SECOND)), t(0), t(2));
        assert_eq!(decision, ExpiryDecision::RequeueAt(t(10)));
    }

    /// EndTime already in the past → immediate expiry.
    // go: lease_controller_test.go:1549-1566 ("EndTime already in the past")
    #[test]
    fn past_end_time_expires_immediately() {
        let decision = evaluate_expiry(None, Some(t(-1)), None, t(-2), t(0));
        assert_eq!(decision, ExpiryDecision::Expire);
    }

    /// Zero-time quirk: no expiration fields at all → Go zero time → immediate
    /// expiry (never a requeue).
    // go: lease_controller.go:145-158 (none-set ⇒ zero ⇒ Before(now))
    #[test]
    fn no_fields_set_expires_immediately() {
        let decision = evaluate_expiry(None, None, None, t(0), t(1));
        assert_eq!(decision, ExpiryDecision::Expire);
    }

    /// Boundary: `expiration == now` requeues (Go uses strict `Before`, so
    /// equal is *not* expired).
    #[test]
    fn expiration_equal_now_requeues() {
        let decision = evaluate_expiry(None, Some(t(5)), None, t(0), t(5));
        assert_eq!(decision, ExpiryDecision::RequeueAt(t(5)));
    }
}
