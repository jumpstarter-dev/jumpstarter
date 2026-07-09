//! Cluster-free input views for the pure lease scheduler.
//!
//! The kube-facing reconciler (phase-4 sibling) projects the relevant `Lease`,
//! `Exporter`, `Client`, and `ExporterAccessPolicy` custom resources into these
//! plain structs; the tests build them directly. Keeping the scheduler's inputs
//! free of `kube`/`k8s-openapi` runtime types (only the `LabelSelector` value
//! type and `chrono` timestamps leak through) is what makes `decide::schedule`
//! table-testable without an apiserver.
//!
//! Field provenance is annotated against `controller/api/v1alpha1/*_types.go`
//! and the reconciler in `controller/internal/controller/lease_controller.go`.

use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::LabelSelector;

use jumpstarter_controller_api::exporter::ExporterStatusValue;
use jumpstarter_controller_api::go_duration::GoDuration;

/// The scheduling-relevant projection of a `Lease` whose `status.exporterRef`
/// is still unset (the only state in which `reconcileStatusExporterRef` does
/// scheduling work).
///
/// go: lease_types.go `LeaseSpec` (selector/exporterRef/beginTime/endTime/duration)
#[derive(Clone, Debug, Default, PartialEq)]
pub struct LeaseView {
    /// `spec.selector` — the exporter label selector.
    pub selector: LabelSelector,
    /// `spec.exporterRef.name` — a pinned exporter, if the lease requested one.
    pub exporter_ref: Option<String>,
    /// `spec.beginTime` — the requested (scheduled) start time.
    pub begin_time: Option<DateTime<Utc>>,
    /// `spec.endTime` — the requested end time.
    pub end_time: Option<DateTime<Utc>>,
    /// `spec.duration` — the requested lease duration.
    pub duration: Option<GoDuration>,
}

/// The scheduling-relevant projection of an `Exporter`.
///
/// `online`/`registered` are pre-derived by the reconciler from the
/// `Online`/`Registered` status conditions (`meta.IsStatusConditionTrue`), so
/// the scheduler needs no condition machinery.
///
/// go: exporter_types.go `ExporterStatus`; conditions read in
/// go: lease_controller.go:571-586 `filterOutOfflineExporters`
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ExporterView {
    /// `metadata.name`.
    pub name: String,
    /// `metadata.labels`.
    pub labels: BTreeMap<String, String>,
    /// Whether the `Online` status condition is `True`.
    pub online: bool,
    /// Whether the `Registered` status condition is `True`.
    pub registered: bool,
    /// `status.exporterStatus` (defaults to `Unspecified` when unset, which the
    /// readiness filter treats the same as the empty Go value).
    pub exporter_status: ExporterStatusValue,
}

/// The scheduling-relevant projection of a `Client` (only its labels feed
/// policy `from.clientSelector` matching).
///
/// go: lease_controller.go:390-396 (client fetched for policy matching)
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ClientView {
    /// `metadata.labels`.
    pub labels: BTreeMap<String, String>,
}

/// A single access-policy rule (`ExporterAccessPolicySpec.policies[i]`).
///
/// go: exporteraccesspolicy_types.go `Policy`
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PolicyRuleView {
    /// `from[].clientSelector` — the client label selectors this rule applies to.
    pub from: Vec<LabelSelector>,
    /// `priority` — higher wins during ordering.
    pub priority: i64,
    /// `spotAccess` — whether this rule grants preemptible (spot) access.
    pub spot_access: bool,
    /// `maximumDuration` — caps the requested lease duration; exceeding it skips
    /// the rule.
    pub maximum_duration: Option<GoDuration>,
    /// `description` — surfaced (deduplicated) in the `NoAccess` message when the
    /// client fails to match.
    pub description: String,
}

/// The scheduling-relevant projection of an `ExporterAccessPolicy`.
///
/// go: exporteraccesspolicy_types.go `ExporterAccessPolicySpec`
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PolicyView {
    /// `spec.exporterSelector` — the exporters this policy governs.
    pub exporter_selector: LabelSelector,
    /// `spec.policies` — the ordered list of access rules.
    pub rules: Vec<PolicyRuleView>,
}

/// The scheduling-relevant projection of an *active* (not-ended) `Lease` other
/// than the one being scheduled, used to detect exporters that are already
/// leased.
///
/// go: lease_controller.go:465-492 `ListActiveLeases` / `attachExistingLeases`
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ActiveLeaseView {
    /// `status.exporterRef.name` of the active lease, if it holds an exporter.
    pub exporter_ref: Option<String>,
    /// `status.spotAccess` — whether the active lease itself holds spot access
    /// (a non-spot request may preempt a spot holder).
    pub spot_access: bool,
}
