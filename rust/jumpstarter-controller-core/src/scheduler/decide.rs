//! The pure lease-scheduling decision core: a faithful, cluster-free port of
//! `reconcileStatusExporterRef` and its helpers
//! (go: lease_controller.go:185-361, 366-586).
//!
//! [`schedule`] re-derives the scheduling decision from the current lease view
//! each pass (level-triggered — no resident state) and returns a
//! [`ScheduleOutcome`] describing the status mutation and/or requeue the
//! reconciler should apply. It performs **no** kube I/O, so the exact Go
//! pipeline — selector match → policy approval → offline filter → attach
//! existing leases → order → spot check → leased filter → not-ready filter →
//! pick → existing-lease spot-pending — is reproduced and table-tested here.
//!
//! Two Go quirks are preserved deliberately and called out at their sites:
//! - the `slices.DeleteFunc` backing-array zeroing means the `Offline` and
//!   all-leased `NotAvailable` messages interpolate an **empty** exporter name
//!   (`"(i.e. )"`); the surviving *count* is the pre-filter length.
//! - the ordering comparator is a total order down to exporter name, so an
//!   unstable sort is exact.

use std::collections::BTreeSet;
use std::time::Duration;

use chrono::{DateTime, Utc};

use jumpstarter_controller_api::exporter::ExporterStatusValue;

use super::selector::{format_label_selector, selector_is_empty, selector_matches};
use super::views::{ActiveLeaseView, ClientView, ExporterView, LeaseView, PolicyView};

// Condition reason strings, VERBATIM from lease_controller.go. `Invalid`
// reasons feed `SetStatusInvalid`, the rest `SetStatusUnsatisfiable` /
// `SetStatusPending` as noted on each `ScheduleOutcome` variant.
/// go: lease_controller.go:218
pub const REASON_INVALID_SELECTOR: &str = "InvalidSelector";
/// go: lease_controller.go:231
pub const REASON_EXPORTER_NOT_FOUND: &str = "ExporterNotFound";
/// go: lease_controller.go:241
pub const REASON_SELECTOR_MISMATCH: &str = "SelectorMismatch";
/// go: lease_controller.go:269/274
pub const REASON_NO_ACCESS: &str = "NoAccess";
/// go: lease_controller.go:285
pub const REASON_OFFLINE: &str = "Offline";
/// go: lease_controller.go:312/345
pub const REASON_NOT_AVAILABLE: &str = "NotAvailable";
/// go: lease_controller.go:324
pub const REASON_NOT_READY: &str = "NotReady";
/// go: lease_controller.go:304
pub const REASON_SPOT_ACCESS: &str = "SpotAccess";

/// The reconciler-facing result of one scheduling pass.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ScheduleOutcome {
    /// Nothing to do this pass (no status change, no requeue). Not produced by
    /// the current Go pipeline for an unassigned lease, but kept as the
    /// absorbing/identity outcome.
    NoChange,
    /// A scheduled lease whose `spec.beginTime` is still in the future: wait the
    /// given duration without touching status.
    /// go: lease_controller.go:199-210
    WaitUntilBegin(Duration),
    /// Set the `Invalid` condition (`SetStatusInvalid`).
    /// go: lease_controller.go:218
    Invalid {
        /// Condition reason (e.g. [`REASON_INVALID_SELECTOR`]).
        reason: &'static str,
        /// Condition message.
        message: String,
    },
    /// Set the `Unsatisfiable` condition (`SetStatusUnsatisfiable`), no requeue.
    /// go: lease_controller.go:230/240/269/304
    Unsatisfiable {
        /// Condition reason.
        reason: &'static str,
        /// Condition message.
        message: String,
    },
    /// Set the `Pending` condition (`SetStatusPending`) and requeue.
    /// go: lease_controller.go:284/312/323/345
    Pending {
        /// Condition reason.
        reason: &'static str,
        /// Condition message.
        message: String,
        /// `result.RequeueAfter` (always one second in the Go pipeline).
        requeue: Duration,
    },
    /// Assign the exporter to the lease (`status.exporterRef`/`priority`/
    /// `spotAccess`).
    /// go: lease_controller.go:352-356
    Assign {
        /// The selected exporter's `metadata.name`.
        exporter: String,
        /// `status.priority` from the approving policy rule.
        priority: i64,
        /// `status.spotAccess` from the approving policy rule.
        spot: bool,
    },
}

/// The Pending requeue interval used throughout `reconcileStatusExporterRef`
/// (`result.RequeueAfter = time.Second`).
const PENDING_REQUEUE: Duration = Duration::from_secs(1);

/// An exporter approved by policy, mirroring Go's `ApprovedExporter`.
struct Approved<'a> {
    exporter: &'a ExporterView,
    priority: i64,
    spot: bool,
    /// `None` when no active lease holds this exporter; `Some(spot)` carries the
    /// holding lease's `status.spotAccess` (a non-spot request may preempt a
    /// spot holder).
    existing_lease_spot: Option<bool>,
}

/// Port of `reconcileStatusExporterRef` for a lease with no `status.exporterRef`
/// yet (the only state in which it schedules). `exporters`, `policies`, and
/// `active_leases` are the full in-namespace sets; `client` supplies the labels
/// used for policy matching (empty is fine when there are no policies, matching
/// Go's "skip the client fetch" optimization).
///
/// go: lease_controller.go:185-361
pub fn schedule(
    lease: &LeaseView,
    exporters: &[ExporterView],
    policies: &[PolicyView],
    client: &ClientView,
    active_leases: &[ActiveLeaseView],
    now: DateTime<Utc>,
) -> ScheduleOutcome {
    // For scheduled leases: only assign once the requested BeginTime arrives.
    // go: lease_controller.go:198-211
    if let Some(begin) = lease.begin_time {
        if begin > now {
            let wait = (begin - now).to_std().unwrap_or(Duration::ZERO);
            return ScheduleOutcome::WaitUntilBegin(wait);
        }
    }

    // Empty selector with no pinned exporter is invalid.
    // go: lease_controller.go:214-220
    let selector_empty = selector_is_empty(&lease.selector);
    if selector_empty && lease.exporter_ref.is_none() {
        return ScheduleOutcome::Invalid {
            reason: REASON_INVALID_SELECTOR,
            message: "The selector for the lease is empty, a selector is required".to_owned(),
        };
    }

    // Resolve the set of matching exporters (pinned exporter vs. selector list).
    // go: lease_controller.go:222-256
    let matching: Vec<&ExporterView> = if let Some(pinned) = &lease.exporter_ref {
        match exporters.iter().find(|e| &e.name == pinned) {
            None => {
                return ScheduleOutcome::Unsatisfiable {
                    reason: REASON_EXPORTER_NOT_FOUND,
                    message: format!("Requested exporter {pinned} was not found"),
                };
            }
            Some(exporter) => {
                if !selector_empty && !selector_matches(&lease.selector, &exporter.labels) {
                    return ScheduleOutcome::Unsatisfiable {
                        reason: REASON_SELECTOR_MISMATCH,
                        message: format!(
                            "Requested exporter {} does not match selector {}",
                            exporter.name,
                            format_label_selector(&lease.selector)
                        ),
                    };
                }
                vec![exporter]
            }
        }
    } else {
        exporters
            .iter()
            .filter(|e| selector_matches(&lease.selector, &e.labels))
            .collect()
    };

    // Attach matching policies (approve-all when there are no policies).
    // go: lease_controller.go:366-447
    let (approved, unmatched_descriptions) =
        attach_matching_policies(lease, &matching, policies, client);

    // go: lease_controller.go:263-280
    if approved.is_empty() {
        let matching_count = matching.len();
        let message = if unmatched_descriptions.is_empty() {
            format!(
                "While there are {matching_count} exporters matching the selector, \
                 none of them are approved by any policy for your client"
            )
        } else {
            let mut desc = unmatched_descriptions.join("; ");
            // go: lease_controller.go:266-268 slices `desc[:4096]` (raw bytes)
            // then appends "...". Truncate at the largest char boundary <= 4096
            // to avoid a mid-codepoint panic; this only differs from Go's byte
            // slice in the rare case a multibyte rune straddles offset 4096.
            if desc.len() > 4096 {
                let mut end = 4096;
                while !desc.is_char_boundary(end) {
                    end -= 1;
                }
                desc.truncate(end);
                desc.push_str("...");
            }
            format!(
                "While there are {matching_count} exporters matching the selector, \
                 none of them are approved by any policy for your client. Matching policies: {desc}"
            )
        };
        return ScheduleOutcome::Unsatisfiable {
            reason: REASON_NO_ACCESS,
            message,
        };
    }

    // Offline filter: keep only Registered && Online exporters.
    // go: lease_controller.go:282-292
    let approved_count = approved.len();
    let mut online: Vec<Approved> = approved
        .into_iter()
        .filter(|a| a.exporter.registered && a.exporter.online)
        .collect();
    if online.is_empty() {
        // Quirk: Go reads approvedExporters[0].Name after slices.DeleteFunc has
        // zeroed the backing array, so the interpolated name is empty; the count
        // is the pre-filter approved length.
        return ScheduleOutcome::Pending {
            reason: REASON_OFFLINE,
            message: format!(
                "While there are {approved_count} available exporters (i.e. ), \
                 none of them are online"
            ),
            requeue: PENDING_REQUEUE,
        };
    }

    // Attach existing (active) leases, then order.
    // go: lease_controller.go:294-301, 482-531
    attach_existing_leases(&mut online, active_leases);
    order_approved(&mut online);

    // Spot check on the best candidate.
    // go: lease_controller.go:303-308
    if online[0].spot {
        return ScheduleOutcome::Unsatisfiable {
            reason: REASON_SPOT_ACCESS,
            message: format!(
                "The only possible exporters are under spot access (i.e. {}), \
                 but spot access is still not implemented",
                online[0].exporter.name
            ),
        };
    }

    // Leased filter.
    // go: lease_controller.go:310-319, 534-553
    let online_count = online.len();
    let available: Vec<&Approved> = online.iter().filter(|a| keep_unleased(a)).collect();
    if available.is_empty() {
        // Same DeleteFunc zeroing quirk as Offline: empty name, pre-filter count.
        return ScheduleOutcome::Pending {
            reason: REASON_NOT_AVAILABLE,
            message: format!(
                "There are {online_count} approved exporters, (i.e. ) \
                 but all of them are already leased"
            ),
            requeue: PENDING_REQUEUE,
        };
    }

    // Not-ready filter (Go clones first, so `available`'s length is intact).
    // go: lease_controller.go:321-330, 555-569
    let available_count = available.len();
    let ready: Vec<&&Approved> = available.iter().filter(|a| is_ready(a.exporter)).collect();
    if ready.is_empty() {
        return ScheduleOutcome::Pending {
            reason: REASON_NOT_READY,
            message: format!(
                "There are {available_count} online exporters, \
                 but none are ready (still cleaning up previous lease)"
            ),
            requeue: PENDING_REQUEUE,
        };
    }

    // Select the best ready exporter.
    // go: lease_controller.go:341-357
    let selected = ready[0];
    if selected.existing_lease_spot.is_some() {
        // Existing spot lease we could preempt, but eviction is unimplemented.
        return ScheduleOutcome::Pending {
            reason: REASON_NOT_AVAILABLE,
            message: format!(
                "Exporter {} is already leased by another client under spot access, \
                 but spot access eviction still not implemented",
                selected.exporter.name
            ),
            requeue: PENDING_REQUEUE,
        };
    }

    ScheduleOutcome::Assign {
        exporter: selected.exporter.name.clone(),
        priority: selected.priority,
        spot: selected.spot,
    }
}

/// Port of `attachMatchingPolicies` (go: lease_controller.go:366-447).
fn attach_matching_policies<'a>(
    lease: &LeaseView,
    matching: &[&'a ExporterView],
    policies: &[PolicyView],
    client: &ClientView,
) -> (Vec<Approved<'a>>, Vec<String>) {
    // No policies ⇒ approve all matching exporters with the default rule.
    // go: lease_controller.go:377-388
    if policies.is_empty() {
        let approved = matching
            .iter()
            .map(|exporter| Approved {
                exporter,
                priority: 0,
                spot: false,
                existing_lease_spot: None,
            })
            .collect();
        return (approved, Vec::new());
    }

    let requested = requested_duration_nanos(lease);
    let mut approved: Vec<Approved> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut descriptions: Vec<String> = Vec::new();

    for exporter in matching {
        for policy in policies {
            if !selector_matches(&policy.exporter_selector, &exporter.labels) {
                continue;
            }
            for rule in &policy.rules {
                let mut client_matched = false;
                for from in &rule.from {
                    if selector_matches(from, &client.labels) {
                        client_matched = true;
                        if let Some(max) = rule.maximum_duration {
                            // Requested duration exceeds the cap ⇒ skip this
                            // `from` (but the client still matched, so no
                            // description is recorded). go: lease_controller.go:417-431
                            if requested > max.0 {
                                continue;
                            }
                        }
                        approved.push(Approved {
                            exporter,
                            priority: rule.priority,
                            spot: rule.spot_access,
                            existing_lease_spot: None,
                        });
                    }
                }
                // go: lease_controller.go:438-441
                if !client_matched
                    && !rule.description.is_empty()
                    && !seen.contains(&rule.description)
                {
                    seen.insert(rule.description.clone());
                    descriptions.push(rule.description.clone());
                }
            }
        }
    }

    (approved, descriptions)
}

/// The requested lease duration in nanoseconds for a `maximumDuration` check:
/// explicit `spec.duration`, else `endTime - beginTime`, else zero.
///
/// go: lease_controller.go:418-424
fn requested_duration_nanos(lease: &LeaseView) -> i64 {
    if let Some(duration) = lease.duration {
        duration.0
    } else if let (Some(begin), Some(end)) = (lease.begin_time, lease.end_time) {
        (end - begin).num_nanoseconds().unwrap_or(0)
    } else {
        0
    }
}

/// Port of `attachExistingLeases` (go: lease_controller.go:482-492): mark each
/// approved exporter that an active lease already holds.
fn attach_existing_leases(approved: &mut [Approved], active_leases: &[ActiveLeaseView]) {
    for entry in approved.iter_mut() {
        for lease in active_leases {
            if lease.exporter_ref.as_deref() == Some(entry.exporter.name.as_str()) {
                // Go overwrites on each match, so the *last* matching active
                // lease wins; replicate that.
                entry.existing_lease_spot = Some(lease.spot_access);
            }
        }
    }
}

/// Port of `orderApprovedExporters` (go: lease_controller.go:500-531): unleased
/// first, then non-spot, then higher priority, then name ascending. The
/// comparator is a total order down to name, so an unstable sort is exact.
fn order_approved(approved: &mut [Approved]) {
    approved.sort_unstable_by(|a, b| {
        // 1. Exporters without an existing lease come first.
        match (
            a.existing_lease_spot.is_some(),
            b.existing_lease_spot.is_some(),
        ) {
            (true, false) => return std::cmp::Ordering::Greater,
            (false, true) => return std::cmp::Ordering::Less,
            _ => {}
        }
        // 2. Non-spot before spot.
        if a.spot != b.spot {
            return if a.spot {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Less
            };
        }
        // 3. Higher priority first.
        if a.priority != b.priority {
            return b.priority.cmp(&a.priority);
        }
        // 4. Name ascending.
        a.exporter.name.cmp(&b.exporter.name)
    });
}

/// Port of the `filterOutLeasedExporters` keep predicate
/// (go: lease_controller.go:534-553): keep if no existing lease, or if we hold
/// non-spot access and the existing lease is itself spot (preemptible).
fn keep_unleased(approved: &Approved) -> bool {
    match approved.existing_lease_spot {
        None => true,
        Some(existing_spot) => {
            let we_have_non_spot = !approved.spot;
            // Keep (can take) if we're non-spot and the holder is spot.
            we_have_non_spot && existing_spot
        }
    }
}

/// Port of the `filterOutNotReadyExporters` keep predicate
/// (go: lease_controller.go:558-569): only `Available` or `Unspecified` (which
/// subsumes the empty Go value) are ready to accept a new lease.
fn is_ready(exporter: &ExporterView) -> bool {
    matches!(
        exporter.exporter_status,
        ExporterStatusValue::Available | ExporterStatusValue::Unspecified
    )
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use k8s_openapi::apimachinery::pkg::apis::meta::v1::LabelSelector;

    use jumpstarter_controller_api::go_duration::{GoDuration, HOUR, SECOND};

    use super::super::selector::parse_label_selector;
    use super::*;

    fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    fn now() -> DateTime<Utc> {
        DateTime::<Utc>::from_timestamp(1_700_000_000, 0).unwrap()
    }

    /// An online, registered, Available exporter with the given dut label.
    fn exporter(name: &str, dut: &str) -> ExporterView {
        ExporterView {
            name: name.to_string(),
            labels: labels(&[("dut", dut)]),
            online: true,
            registered: true,
            exporter_status: ExporterStatusValue::Available,
        }
    }

    fn lease_dut(dut: &str) -> LeaseView {
        LeaseView {
            selector: LabelSelector {
                match_labels: Some(labels(&[("dut", dut)])),
                match_expressions: None,
            },
            duration: Some(GoDuration(2 * SECOND)),
            ..Default::default()
        }
    }

    fn no_client() -> ClientView {
        ClientView::default()
    }

    // -- pinned exporter paths ----------------------------------------------

    // go: lease_controller_test.go:87-105 ("requested exporter and empty selector")
    #[test]
    fn pinned_exporter_empty_selector_assigns() {
        let mut lease = LeaseView {
            duration: Some(GoDuration(2 * SECOND)),
            exporter_ref: Some("exporter1-dut-a".into()),
            ..Default::default()
        };
        lease.selector = LabelSelector::default();
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Assign {
                exporter: "exporter1-dut-a".into(),
                priority: 0,
                spot: false
            }
        );
    }

    // go: lease_controller_test.go:107-130 ("missing requested exporter")
    #[test]
    fn pinned_exporter_missing_is_exporter_not_found() {
        let lease = LeaseView {
            duration: Some(GoDuration(2 * SECOND)),
            exporter_ref: Some("does-not-exist".into()),
            ..Default::default()
        };
        let outcome = schedule(&lease, &[], &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Unsatisfiable {
                reason: REASON_EXPORTER_NOT_FOUND,
                message: "Requested exporter does-not-exist was not found".into(),
            }
        );
    }

    // go: lease_controller_test.go:132-151 ("requested exporter that does not match selector")
    #[test]
    fn pinned_exporter_selector_mismatch() {
        let lease = LeaseView {
            selector: LabelSelector {
                match_labels: Some(labels(&[("dut", "b")])),
                match_expressions: None,
            },
            duration: Some(GoDuration(2 * SECOND)),
            exporter_ref: Some("exporter1-dut-a".into()),
            ..Default::default()
        };
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Unsatisfiable {
                reason: REASON_SELECTOR_MISMATCH,
                message: "Requested exporter exporter1-dut-a does not match selector dut=b".into(),
            }
        );
    }

    // -- empty selector ------------------------------------------------------

    // go: lease_controller_test.go:75-85 ("empty selector") — CEL rejects create,
    // but the scheduler's own guard is InvalidSelector.
    #[test]
    fn empty_selector_no_pin_is_invalid() {
        let lease = LeaseView {
            duration: Some(GoDuration(2 * SECOND)),
            ..Default::default()
        };
        let outcome = schedule(&lease, &[], &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Invalid {
                reason: REASON_INVALID_SELECTOR,
                message: "The selector for the lease is empty, a selector is required".into(),
            }
        );
    }

    // -- happy path ----------------------------------------------------------

    // go: lease_controller_test.go:153-169 ("available exporter") — assigns one
    // of the matching exporters (deterministically the name-least here).
    #[test]
    fn available_exporter_assigns_name_least() {
        let lease = lease_dut("a");
        let exporters = [
            exporter("exporter2-dut-a", "a"),
            exporter("exporter1-dut-a", "a"),
        ];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Assign {
                exporter: "exporter1-dut-a".into(),
                priority: 0,
                spot: false
            }
        );
    }

    // go: lease_controller_test.go:201-218 ("non existing exporter") — selector
    // matches nothing ⇒ no approved ⇒ NoAccess (no policies, no descriptions).
    #[test]
    fn selector_matches_nothing_is_no_access_without_descriptions() {
        let lease = lease_dut("does-not-exist");
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Unsatisfiable {
                reason: REASON_NO_ACCESS,
                message: "While there are 0 exporters matching the selector, \
                          none of them are approved by any policy for your client"
                    .into(),
            }
        );
        // go: lease_controller_test.go:543-560 — no "Matching policies:" segment.
        if let ScheduleOutcome::Unsatisfiable { message, .. } = outcome {
            assert!(!message.contains("Matching policies:"));
        }
    }

    // -- offline -------------------------------------------------------------

    // go: lease_controller_test.go:220-245 ("offline exporter") — note the empty
    // interpolated name quirk and the pre-filter count of 2.
    #[test]
    fn all_offline_is_pending_offline_with_empty_name() {
        let lease = lease_dut("a");
        let mut e1 = exporter("exporter1-dut-a", "a");
        let mut e2 = exporter("exporter2-dut-a", "a");
        for e in [&mut e1, &mut e2] {
            e.online = false;
            e.registered = false;
        }
        let exporters = [e1, e2];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Pending {
                reason: REASON_OFFLINE,
                message: "While there are 2 available exporters (i.e. ), none of them are online"
                    .into(),
                requeue: Duration::from_secs(1),
            }
        );
    }

    // go: lease_controller_test.go:562-582 ("some online, some offline") — only
    // the online one is eligible.
    #[test]
    fn mixed_online_offline_assigns_online() {
        let lease = lease_dut("a");
        let mut e1 = exporter("exporter1-dut-a", "a");
        e1.online = false;
        e1.registered = false;
        let e2 = exporter("exporter2-dut-a", "a");
        let exporters = [e1, e2];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Assign {
                exporter: "exporter2-dut-a".into(),
                priority: 0,
                spot: false
            }
        );
    }

    // Offline requires BOTH Registered AND Online: registered-but-not-online is
    // still offline. go: lease_controller.go:576-583
    #[test]
    fn registered_but_not_online_is_offline() {
        let lease = lease_dut("a");
        let mut e = exporter("exporter1-dut-a", "a");
        e.online = false; // registered stays true
        let outcome = schedule(&lease, &[e], &[], &no_client(), &[], now());
        assert!(matches!(
            outcome,
            ScheduleOutcome::Pending {
                reason: REASON_OFFLINE,
                ..
            }
        ));
    }

    // -- not ready -----------------------------------------------------------

    // go: lease_controller_test.go:584-614 ("online but not ready") — both in a
    // hook status ⇒ NotReady with count 2.
    #[test]
    fn all_not_ready_is_pending_not_ready() {
        let lease = lease_dut("a");
        let mut e1 = exporter("exporter1-dut-a", "a");
        e1.exporter_status = ExporterStatusValue::AfterLeaseHook;
        let mut e2 = exporter("exporter2-dut-a", "a");
        e2.exporter_status = ExporterStatusValue::BeforeLeaseHook;
        let exporters = [e1, e2];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Pending {
                reason: REASON_NOT_READY,
                message: "There are 2 online exporters, but none are ready \
                          (still cleaning up previous lease)"
                    .into(),
                requeue: Duration::from_secs(1),
            }
        );
    }

    // go: lease_controller_test.go:616-632 ("some ready, some not") — only the
    // ready one is selected.
    #[test]
    fn one_ready_one_not_selects_ready() {
        let lease = lease_dut("a");
        let mut e1 = exporter("exporter1-dut-a", "a");
        e1.exporter_status = ExporterStatusValue::AfterLeaseHook;
        let e2 = exporter("exporter2-dut-a", "a");
        let exporters = [e1, e2];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Assign {
                exporter: "exporter2-dut-a".into(),
                priority: 0,
                spot: false
            }
        );
    }

    // Unset/"" exporter status is treated as ready (backwards compat).
    // go: lease_controller.go:563-566
    #[test]
    fn unspecified_status_is_ready() {
        let lease = lease_dut("a");
        let mut e = exporter("exporter1-dut-a", "a");
        e.exporter_status = ExporterStatusValue::Unspecified;
        let outcome = schedule(&lease, &[e], &[], &no_client(), &[], now());
        assert!(matches!(outcome, ScheduleOutcome::Assign { .. }));
    }

    // -- already leased (NotAvailable) --------------------------------------

    // go: lease_controller_test.go:634-672 ("busy exporter") — the only dut:b
    // exporter is leased non-spot ⇒ NotAvailable, empty name, count 1.
    #[test]
    fn only_exporter_leased_is_not_available() {
        let lease = lease_dut("b");
        let exporters = [exporter("exporter3-dut-b", "b")];
        let active = [ActiveLeaseView {
            exporter_ref: Some("exporter3-dut-b".into()),
            spot_access: false,
        }];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &active, now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Pending {
                reason: REASON_NOT_AVAILABLE,
                message:
                    "There are 1 approved exporters, (i.e. ) but all of them are already leased"
                        .into(),
                requeue: Duration::from_secs(1),
            }
        );
    }

    // A non-spot request may preempt a spot holder, but eviction is
    // unimplemented ⇒ per-exporter NotAvailable with the real name.
    // go: lease_controller.go:343-350
    #[test]
    fn spot_held_exporter_is_pending_not_available_with_name() {
        let lease = lease_dut("b");
        let exporters = [exporter("exporter3-dut-b", "b")];
        let active = [ActiveLeaseView {
            exporter_ref: Some("exporter3-dut-b".into()),
            spot_access: true, // held under spot ⇒ preemptible ⇒ survives leased filter
        }];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &active, now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Pending {
                reason: REASON_NOT_AVAILABLE,
                message: "Exporter exporter3-dut-b is already leased by another client \
                          under spot access, but spot access eviction still not implemented"
                    .into(),
                requeue: Duration::from_secs(1),
            }
        );
    }

    // -- scheduled (WaitUntilBegin) -----------------------------------------

    // go: lease_controller_test.go:1066-1096 ("BeginTime + Duration") — future
    // begin ⇒ wait, no status change.
    #[test]
    fn future_begin_time_waits() {
        let mut lease = lease_dut("a");
        lease.begin_time = Some(now() + chrono::Duration::seconds(5));
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::WaitUntilBegin(Duration::from_secs(5))
        );
    }

    // go: lease_controller_test.go:1304-1323 ("BeginTime already in the past") —
    // past begin ⇒ proceeds to assign.
    #[test]
    fn past_begin_time_assigns() {
        let mut lease = lease_dut("a");
        lease.begin_time = Some(now() - chrono::Duration::seconds(2));
        lease.duration = Some(GoDuration(SECOND));
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &[], &no_client(), &[], now());
        assert!(matches!(outcome, ScheduleOutcome::Assign { .. }));
    }

    // -- policy approval -----------------------------------------------------

    fn policy(exporter_dut: &str, rules: Vec<PolicyRuleView>) -> PolicyView {
        PolicyView {
            exporter_selector: LabelSelector {
                match_labels: Some(labels(&[("dut", exporter_dut)])),
                match_expressions: None,
            },
            rules,
        }
    }

    fn client_from(pairs: &[(&str, &str)]) -> LabelSelector {
        LabelSelector {
            match_labels: Some(labels(pairs)),
            match_expressions: None,
        }
    }

    // go: lease_controller_test.go:309-367 ("match selector but not approved") —
    // client selector doesn't match ⇒ NoAccess with the description.
    #[test]
    fn policy_client_mismatch_is_no_access_with_description() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "different-client")])],
                priority: 0,
                spot_access: false,
                maximum_duration: None,
                description: "Requires different-client label".into(),
            }],
        )];
        let exporters = [
            exporter("exporter1-dut-a", "a"),
            exporter("exporter2-dut-a", "a"),
        ];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        match outcome {
            ScheduleOutcome::Unsatisfiable { reason, message } => {
                assert_eq!(reason, REASON_NO_ACCESS);
                assert!(
                    message.contains("none of them are approved by any policy"),
                    "{message}"
                );
                assert!(
                    message.contains("Requires different-client label"),
                    "{message}"
                );
                assert!(message.contains("Matching policies:"), "{message}");
            }
            other => panic!("expected Unsatisfiable, got {other:?}"),
        }
    }

    // go: lease_controller_test.go:369-440 ("multiple policies, none matching") —
    // both descriptions present, joined by "; ".
    #[test]
    fn multiple_unmatched_descriptions_are_joined_and_deduped() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [
            policy(
                "a",
                vec![PolicyRuleView {
                    from: vec![client_from(&[("role", "admin")])],
                    priority: 20,
                    spot_access: false,
                    maximum_duration: None,
                    description: "Administrators only".into(),
                }],
            ),
            policy(
                "a",
                vec![PolicyRuleView {
                    from: vec![client_from(&[("role", "ci")])],
                    priority: 5,
                    spot_access: false,
                    maximum_duration: None,
                    description: "CI pipelines only".into(),
                }],
            ),
        ];
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        match outcome {
            ScheduleOutcome::Unsatisfiable { reason, message } => {
                assert_eq!(reason, REASON_NO_ACCESS);
                assert!(message.contains("Administrators only"), "{message}");
                assert!(message.contains("CI pipelines only"), "{message}");
                assert!(message.contains(';'), "{message}");
            }
            other => panic!("expected Unsatisfiable, got {other:?}"),
        }
    }

    // go: lease_controller_test.go:442-495 ("some empty descriptions") — only the
    // non-empty description is reported, but "Matching policies:" appears.
    #[test]
    fn empty_descriptions_are_omitted() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![
                PolicyRuleView {
                    from: vec![client_from(&[("role", "vip")])],
                    priority: 10,
                    spot_access: false,
                    maximum_duration: None,
                    description: "VIP access rule".into(),
                },
                PolicyRuleView {
                    from: vec![client_from(&[("role", "other")])],
                    priority: 1,
                    spot_access: false,
                    maximum_duration: None,
                    description: String::new(),
                },
            ],
        )];
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        match outcome {
            ScheduleOutcome::Unsatisfiable { message, .. } => {
                assert!(message.contains("VIP access rule"), "{message}");
                assert!(message.contains("Matching policies:"), "{message}");
            }
            other => panic!("expected Unsatisfiable, got {other:?}"),
        }
    }

    // go: lease_controller_test.go:497-541 ("policy matches the client") — the
    // approving policy's priority is carried into the assignment.
    #[test]
    fn matching_policy_assigns_with_priority() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "client")])],
                priority: 10,
                spot_access: false,
                maximum_duration: None,
                description: "Standard access for registered clients".into(),
            }],
        )];
        let exporters = [
            exporter("exporter1-dut-a", "a"),
            exporter("exporter2-dut-a", "a"),
        ];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        match outcome {
            ScheduleOutcome::Assign { priority, spot, .. } => {
                assert_eq!(priority, 10);
                assert!(!spot);
            }
            other => panic!("expected Assign, got {other:?}"),
        }
    }

    // Offline-but-approved policy path ⇒ Pending Offline with "none of them are
    // online". go: lease_controller_test.go:247-307
    #[test]
    fn approved_but_offline_is_pending_offline() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "client")])],
                priority: 0,
                spot_access: false,
                maximum_duration: None,
                description: String::new(),
            }],
        )];
        let mut e1 = exporter("exporter1-dut-a", "a");
        let mut e2 = exporter("exporter2-dut-a", "a");
        for e in [&mut e1, &mut e2] {
            e.online = false;
            e.registered = false;
        }
        let outcome = schedule(&lease, &[e1, e2], &policies, &client, &[], now());
        match outcome {
            ScheduleOutcome::Pending {
                reason, message, ..
            } => {
                assert_eq!(reason, REASON_OFFLINE);
                assert!(message.contains("none of them are online"), "{message}");
            }
            other => panic!("expected Pending Offline, got {other:?}"),
        }
    }

    // maximumDuration: a rule whose cap is exceeded is skipped (continue), so the
    // exporter is not approved by it. go: lease_controller.go:417-431
    #[test]
    fn maximum_duration_exceeded_skips_rule() {
        // Lease requests 2h; the only rule caps at 1h ⇒ not approved ⇒ NoAccess.
        let mut lease = lease_dut("a");
        lease.duration = Some(GoDuration(2 * HOUR));
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "client")])],
                priority: 0,
                spot_access: false,
                maximum_duration: Some(GoDuration(HOUR)),
                description: String::new(),
            }],
        )];
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        // Client matched (so no description), but no approval ⇒ NoAccess with no
        // "Matching policies:" segment.
        match outcome {
            ScheduleOutcome::Unsatisfiable { reason, message } => {
                assert_eq!(reason, REASON_NO_ACCESS);
                assert!(!message.contains("Matching policies:"), "{message}");
            }
            other => panic!("expected Unsatisfiable NoAccess, got {other:?}"),
        }
    }

    // maximumDuration honored when within the cap ⇒ approved.
    #[test]
    fn maximum_duration_within_cap_approves() {
        let mut lease = lease_dut("a");
        lease.duration = Some(GoDuration(30 * 60 * SECOND)); // 30m
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "client")])],
                priority: 3,
                spot_access: false,
                maximum_duration: Some(GoDuration(HOUR)),
                description: String::new(),
            }],
        )];
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        assert!(matches!(
            outcome,
            ScheduleOutcome::Assign { priority: 3, .. }
        ));
    }

    // -- spot access ---------------------------------------------------------

    // When the only approving rule is spot access, the best candidate is spot ⇒
    // Unsatisfiable SpotAccess (with the real name). go: lease_controller.go:303-308
    #[test]
    fn only_spot_access_is_unsatisfiable() {
        let lease = lease_dut("a");
        let client = ClientView {
            labels: labels(&[("name", "client")]),
        };
        let policies = [policy(
            "a",
            vec![PolicyRuleView {
                from: vec![client_from(&[("name", "client")])],
                priority: 0,
                spot_access: true,
                maximum_duration: None,
                description: String::new(),
            }],
        )];
        let exporters = [exporter("exporter1-dut-a", "a")];
        let outcome = schedule(&lease, &exporters, &policies, &client, &[], now());
        assert_eq!(
            outcome,
            ScheduleOutcome::Unsatisfiable {
                reason: REASON_SPOT_ACCESS,
                message: "The only possible exporters are under spot access \
                          (i.e. exporter1-dut-a), but spot access is still not implemented"
                    .into(),
            }
        );
    }

    // -- selector parsing round-trip into scheduling ------------------------

    // A parsed string selector drives matching just like a structured one.
    #[test]
    fn parsed_selector_drives_matching() {
        let mut lease = lease_dut("a");
        lease.selector = parse_label_selector("dut=a,revision!=v3").unwrap();
        let mut e = exporter("exporter1-dut-a", "a");
        e.labels.insert("revision".into(), "v3".into());
        // revision=v3 excluded by the selector ⇒ nothing matches ⇒ NoAccess.
        let outcome = schedule(&lease, &[e], &[], &no_client(), &[], now());
        assert!(matches!(
            outcome,
            ScheduleOutcome::Unsatisfiable {
                reason: REASON_NO_ACCESS,
                ..
            }
        ));
    }

    // ==== orderApprovedExporters (go: lease_controller_test.go:892-1028) =====

    use super::super::views::PolicyRuleView;

    fn approved_for<'a>(
        exporter: &'a ExporterView,
        priority: i64,
        spot: bool,
        existing_lease_spot: Option<bool>,
    ) -> Approved<'a> {
        Approved {
            exporter,
            priority,
            spot,
            existing_lease_spot,
        }
    }

    // go: lease_controller_test.go:893-912 ("under a lease ⇒ last")
    #[test]
    fn order_puts_leased_last() {
        let e1 = exporter("exporter1-dut-a", "a");
        let e2 = exporter("exporter2-dut-a", "a");
        let mut approved = vec![
            approved_for(&e1, 0, false, Some(false)),
            approved_for(&e2, 0, false, None),
        ];
        order_approved(&mut approved);
        assert_eq!(approved[0].exporter.name, "exporter2-dut-a");
        assert!(approved[0].existing_lease_spot.is_none());
        assert_eq!(approved[1].exporter.name, "exporter1-dut-a");
        assert!(approved[1].existing_lease_spot.is_some());
    }

    // go: lease_controller_test.go:914-934 ("spot mode ⇒ last")
    #[test]
    fn order_puts_spot_last() {
        let e1 = exporter("exporter1-dut-a", "a");
        let e2 = exporter("exporter2-dut-a", "a");
        let mut approved = vec![
            approved_for(&e1, 0, true, Some(false)),
            approved_for(&e2, 0, false, Some(false)),
        ];
        order_approved(&mut approved);
        assert_eq!(approved[0].exporter.name, "exporter2-dut-a");
        assert!(!approved[0].spot);
        assert_eq!(approved[1].exporter.name, "exporter1-dut-a");
        assert!(approved[1].spot);
    }

    // go: lease_controller_test.go:936-958 ("by priority")
    #[test]
    fn order_by_priority_desc() {
        let e1 = exporter("exporter1-dut-a", "a");
        let e2 = exporter("exporter2-dut-a", "a");
        let mut approved = vec![
            approved_for(&e1, 5, false, None),
            approved_for(&e2, 10, false, None),
            approved_for(&e2, 100, false, None),
        ];
        order_approved(&mut approved);
        assert_eq!(approved[0].priority, 100);
        assert_eq!(approved[1].priority, 10);
        assert_eq!(approved[2].priority, 5);
    }

    // go: lease_controller_test.go:960-977 ("same priority ⇒ by name")
    #[test]
    fn order_same_priority_by_name() {
        let e1 = exporter("exporter1-dut-a", "a");
        let e2 = exporter("exporter2-dut-a", "a");
        let mut approved = vec![
            approved_for(&e2, 5, false, None),
            approved_for(&e1, 5, false, None),
        ];
        order_approved(&mut approved);
        assert_eq!(approved[0].exporter.name, "exporter1-dut-a");
        assert_eq!(approved[1].exporter.name, "exporter2-dut-a");
    }

    // go: lease_controller_test.go:979-1027 ("mixed priorities, spot, lease")
    #[test]
    fn order_mixed() {
        let e1 = exporter("exporter1-dut-a", "a");
        let e2 = exporter("exporter2-dut-a", "a");
        let mut approved = vec![
            approved_for(&e2, 5, false, None),
            approved_for(&e2, 100, true, Some(false)),
            approved_for(&e1, 10, false, None),
            approved_for(&e1, 5, false, None),
            approved_for(&e2, 10, true, None),
        ];
        order_approved(&mut approved);
        // [0] priority 10, non-spot, exporter1
        assert_eq!(approved[0].priority, 10);
        assert!(!approved[0].spot);
        assert_eq!(approved[0].exporter.name, "exporter1-dut-a");
        // [1] priority 5, non-spot, exporter1
        assert_eq!(approved[1].priority, 5);
        assert!(!approved[1].spot);
        assert_eq!(approved[1].exporter.name, "exporter1-dut-a");
        // [2] priority 5, non-spot, exporter2
        assert_eq!(approved[2].priority, 5);
        assert!(!approved[2].spot);
        assert_eq!(approved[2].exporter.name, "exporter2-dut-a");
        // [3] priority 10, spot, exporter2
        assert_eq!(approved[3].priority, 10);
        assert!(approved[3].spot);
        assert_eq!(approved[3].exporter.name, "exporter2-dut-a");
        // [4] priority 100, spot, exporter2 (has existing lease ⇒ last)
        assert_eq!(approved[4].priority, 100);
        assert!(approved[4].spot);
        assert_eq!(approved[4].exporter.name, "exporter2-dut-a");
    }
}
