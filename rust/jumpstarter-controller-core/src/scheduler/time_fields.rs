//! Port of `ReconcileLeaseTimeFields` (go: lease_helpers.go:57-78): derive and
//! validate the `BeginTime`/`EndTime`/`Duration` triple.
//!
//! This runs at the gRPC boundary (`LeaseFromProtobuf`, `UpdateLease`), so its
//! error strings cross the wire and are reproduced byte-for-byte. The `%v` in
//! `"duration must be positive, got %v"` is Go's `time.Duration` formatting,
//! delegated to [`GoDuration`]'s `Display` (go: lease_helpers.go:75).
//!
//! The six supported lease-specification patterns are documented on the Go
//! function; the logic here mirrors it exactly:
//!
//! 1. `Duration` only → left as-is (validated positive).
//! 2. `EndTime` only → error, `Duration` is required.
//! 3. `BeginTime` + `Duration` → left as-is.
//! 4. `BeginTime` + `EndTime` → `Duration = EndTime - BeginTime`.
//! 5. `EndTime` + `Duration` → `BeginTime = EndTime - Duration`.
//! 6. all three → validated consistent, else `duration conflicts` error.

use chrono::{DateTime, Duration as ChronoDuration, Utc};

use jumpstarter_controller_api::go_duration::GoDuration;

/// Errors from [`reconcile_lease_time_fields`], worded byte-identically to the
/// Go originals (go: lease_helpers.go:62/72/75).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum TimeFieldsError {
    /// `Duration` was set and positive but disagreed with `EndTime - BeginTime`.
    /// go: lease_helpers.go:62
    #[error("duration conflicts with begin_time and end_time")]
    DurationConflicts,
    /// No `Duration` could be determined (neither explicit nor from both times).
    /// go: lease_helpers.go:72
    #[error("duration is required (must specify Duration, or both BeginTime and EndTime)")]
    DurationRequired,
    /// The resolved `Duration` was zero or negative. `{0}` renders like Go's
    /// `time.Duration` `%v`.
    /// go: lease_helpers.go:75
    #[error("duration must be positive, got {0}")]
    DurationMustBePositive(GoDuration),
}

/// Port of `ReconcileLeaseTimeFields` (go: lease_helpers.go:57-78). Mutates the
/// three fields in place to fill in whichever is derivable, then validates the
/// resulting `duration` is strictly positive.
///
/// go: lease_helpers.go:57-78
pub fn reconcile_lease_time_fields(
    begin_time: &mut Option<DateTime<Utc>>,
    end_time: &mut Option<DateTime<Utc>>,
    duration: &mut Option<GoDuration>,
) -> Result<(), TimeFieldsError> {
    if let (Some(begin), Some(end)) = (*begin_time, *end_time) {
        // Calculate duration from explicit begin/end times.
        let calculated = sub_to_go_duration(end, begin);
        if let Some(d) = *duration {
            if d.0 > 0 && d.0 != calculated.0 {
                return Err(TimeFieldsError::DurationConflicts);
            }
        }
        *duration = Some(calculated);
    } else if let (Some(end), Some(d)) = (*end_time, *duration) {
        if d.0 > 0 {
            // Calculate BeginTime from EndTime - Duration.
            *begin_time = Some(end - ChronoDuration::nanoseconds(d.0));
        }
    }

    // Validate final duration is positive (rejects nil, negative, zero).
    let Some(d) = *duration else {
        return Err(TimeFieldsError::DurationRequired);
    };
    if d.0 <= 0 {
        return Err(TimeFieldsError::DurationMustBePositive(d));
    }
    Ok(())
}

/// `end - begin` as a Go-nanosecond [`GoDuration`], matching
/// `(*metav1.Time).Sub` (an `int64`-nanosecond `time.Duration`).
fn sub_to_go_duration(end: DateTime<Utc>, begin: DateTime<Utc>) -> GoDuration {
    GoDuration(
        (end - begin)
            .num_nanoseconds()
            .expect("lease time delta fits in i64 nanoseconds"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_api::go_duration::SECOND;

    fn t(secs: i64) -> DateTime<Utc> {
        DateTime::<Utc>::from_timestamp(1_700_000_000 + secs, 0).unwrap()
    }

    /// Pattern 1: Duration only — left as-is, accepted.
    // go: lease_controller_test.go:1043-1064 ("Duration only (immediate lease)")
    #[test]
    fn duration_only_is_accepted() {
        let mut begin = None;
        let mut end = None;
        let mut duration = Some(GoDuration(2 * SECOND));
        reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap();
        assert_eq!(begin, None);
        assert_eq!(end, None);
        assert_eq!(duration, Some(GoDuration(2 * SECOND)));
    }

    /// Pattern 3: BeginTime + Duration — left as-is.
    // go: lease_controller_test.go:1066-1096 ("BeginTime + Duration")
    #[test]
    fn begin_time_and_duration_left_as_is() {
        let mut begin = Some(t(1));
        let mut end = None;
        let mut duration = Some(GoDuration(SECOND));
        reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap();
        assert_eq!(begin, Some(t(1)));
        assert_eq!(end, None);
        assert_eq!(duration, Some(GoDuration(SECOND)));
    }

    /// Pattern 4: BeginTime + EndTime — Duration computed from the two times.
    // go: lease_controller_test.go:1098-1132 ("BeginTime + EndTime (without Duration)")
    // go: lease_controller_test.go:1735-1768 ("updating EndTime on a scheduled lease")
    #[test]
    fn begin_and_end_compute_duration() {
        let mut begin = Some(t(1));
        let mut end = Some(t(3));
        let mut duration = None;
        reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap();
        assert_eq!(duration, Some(GoDuration(2 * SECOND)));
        assert_eq!(begin, Some(t(1)));
        assert_eq!(end, Some(t(3)));
    }

    /// Pattern 5: EndTime + Duration — BeginTime computed as EndTime - Duration.
    // go: lease_controller_test.go:1171-1203 ("EndTime + Duration")
    #[test]
    fn end_and_duration_compute_begin() {
        let mut begin = None;
        let mut end = Some(t(2));
        let mut duration = Some(GoDuration(SECOND));
        reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap();
        assert_eq!(begin, Some(t(1)));
        assert_eq!(end, Some(t(2)));
        assert_eq!(duration, Some(GoDuration(SECOND)));
    }

    /// Pattern 6: all three consistent — accepted, values unchanged.
    // go: lease_controller_test.go:1247-1276 / 1880-1913 (consistent update)
    #[test]
    fn all_three_consistent_ok() {
        let mut begin = Some(t(1));
        let mut end = Some(t(2));
        let mut duration = Some(GoDuration(SECOND));
        reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap();
        assert_eq!(duration, Some(GoDuration(SECOND)));
    }

    /// Pattern 6: all three, Duration disagrees with EndTime - BeginTime.
    // go: lease_controller_test.go:1278-1301 ("Duration conflicts")
    // go: lease_controller_test.go:1916-1938 ("create conflict")
    #[test]
    fn all_three_conflict_errors() {
        let mut begin = Some(t(1));
        let mut end = Some(t(2)); // 1s window
        let mut duration = Some(GoDuration(2 * SECOND)); // wrong
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(err, TimeFieldsError::DurationConflicts);
        assert_eq!(
            err.to_string(),
            "duration conflicts with begin_time and end_time"
        );
    }

    /// Pattern 2: EndTime only — Duration required.
    // go: lease_controller_test.go:1527-1546 ("BeginTime but zero Duration and no EndTime")
    #[test]
    fn end_time_only_requires_duration() {
        // EndTime only (no begin, no duration): the else-if guard needs a
        // positive duration, so nothing is derived and the required-error fires.
        let mut begin = None;
        let mut end = Some(t(2));
        let mut duration = None;
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(err, TimeFieldsError::DurationRequired);
        assert_eq!(
            err.to_string(),
            "duration is required (must specify Duration, or both BeginTime and EndTime)"
        );
    }

    /// No fields at all — Duration required.
    #[test]
    fn nothing_set_requires_duration() {
        let mut begin = None;
        let mut end = None;
        let mut duration = None;
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(err, TimeFieldsError::DurationRequired);
    }

    /// BeginTime after EndTime (no duration) → negative computed duration → the
    /// positive-duration validation rejects it, formatting `%v` as Go does.
    // go: lease_controller_test.go:1504-1525 ("BeginTime after EndTime")
    #[test]
    fn begin_after_end_negative_duration_rejected() {
        let mut begin = Some(t(2));
        let mut end = Some(t(1)); // before begin → -1s
        let mut duration = None;
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(
            err,
            TimeFieldsError::DurationMustBePositive(GoDuration(-SECOND))
        );
        assert_eq!(err.to_string(), "duration must be positive, got -1s");
    }

    /// Negative Duration only.
    // go: lease_controller_test.go:2107-2123 ("negative Duration")
    #[test]
    fn negative_duration_rejected() {
        let mut begin = None;
        let mut end = None;
        let mut duration = Some(GoDuration(-SECOND));
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(
            err,
            TimeFieldsError::DurationMustBePositive(GoDuration(-SECOND))
        );
        assert_eq!(err.to_string(), "duration must be positive, got -1s");
    }

    /// EndTime + negative Duration: the else-if `> 0` guard is false, so
    /// BeginTime is not derived; the trailing positive-check rejects the
    /// negative duration (formatting `-2s`).
    // go: lease_controller_test.go:2125-2142 ("EndTime and negative Duration")
    #[test]
    fn end_time_and_negative_duration_rejected() {
        let mut begin = None;
        let mut end = Some(t(1));
        let mut duration = Some(GoDuration(-2 * SECOND));
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(
            begin, None,
            "BeginTime must not be derived for negative duration"
        );
        assert_eq!(err.to_string(), "duration must be positive, got -2s");
    }

    /// Zero Duration is rejected (Go `<= 0`), formatting as `0s`.
    #[test]
    fn zero_duration_rejected() {
        let mut begin = None;
        let mut end = None;
        let mut duration = Some(GoDuration(0));
        let err = reconcile_lease_time_fields(&mut begin, &mut end, &mut duration).unwrap_err();
        assert_eq!(err.to_string(), "duration must be positive, got 0s");
    }
}
