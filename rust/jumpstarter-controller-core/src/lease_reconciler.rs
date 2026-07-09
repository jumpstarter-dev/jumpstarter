//! The Lease reconciler, porting
//! `controller/internal/controller/lease_controller.go` (`Reconcile`).
//!
//! The reconciler is a thin kube-facing shell around the pure, cluster-free
//! decision core in [`crate::scheduler`]. Every pass re-derives the lease state
//! from the CR (level-triggered), mirroring the Go pipeline
//! (go: lease_controller.go:69-122):
//!
//!   1. `reconcileStatusExporterRef` — while unassigned and not ended, project
//!      the matching exporters / policies / client / active leases into the
//!      scheduler views and run [`schedule`], then apply the resulting status
//!      mutation (conditions or `exporterRef`/`priority`/`spotAccess`) and
//!      requeue (go: lease_controller.go:185-361);
//!   2. `reconcileStatusBeginEndTimes` — stamp `status.beginTime` + the `Ready`
//!      condition the first pass after an exporter is acquired
//!      (go: lease_controller.go:169-182);
//!   3. `reconcileStatusEnded` — mark ended on an `Unsatisfiable`/`Invalid`
//!      condition, honor `spec.release`, or compute expiry and its requeue
//!      (go: lease_controller.go:126-166), delegating the expiration arithmetic
//!      to [`evaluate_expiry`];
//!   4. status update → on 409 requeue immediately (Go `RequeueConflict`);
//!   5. set the `jumpstarter.dev/lease-ended` label when ended and the
//!      controller owner reference from the assigned exporter, then the metadata
//!      update → on 409 requeue immediately.
//!
//! Watches: **Leases only**. Go registers no `Owns`/`Watches` on Exporters, so
//! a pending lease makes progress purely through the 1-second `RequeueAfter`
//! poll produced by the scheduler's `Pending` outcomes. An exporter→lease watch
//! mapping would let pending leases react immediately to an exporter coming
//! online; it is a **post-parity optimization** and deliberately omitted here.
//!
//! The three requeue sources (wait-until-begin, pending-1s, future-expiry) are
//! mutually exclusive in the Go pipeline, so they collapse into a single
//! `Option<Duration>` that maps to `Action::requeue(d)` (else
//! `Action::await_change`).

use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use futures::StreamExt;
use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{OwnerReference, Time};
use k8s_openapi::jiff::Timestamp;
use kube::api::{Api, ListParams, PostParams};
use kube::runtime::controller::{Action, Controller};
use kube::runtime::watcher;
use kube::{Client, Resource, ResourceExt};

use jumpstarter_controller_api::access_policy::ExporterAccessPolicy;
use jumpstarter_controller_api::client::Client as ClientCr;
use jumpstarter_controller_api::conditions::{
    EXPORTER_CONDITION_TYPE_ONLINE, EXPORTER_CONDITION_TYPE_REGISTERED,
    LEASE_CONDITION_TYPE_INVALID, LEASE_CONDITION_TYPE_UNSATISFIABLE,
};
use jumpstarter_controller_api::exporter::{Exporter, ExporterStatusValue};
use jumpstarter_controller_api::labels::{LEASE_LABEL_ENDED, LEASE_LABEL_ENDED_VALUE};
use jumpstarter_controller_api::lease::Lease;

use crate::conditions::is_status_condition_true;
use crate::scheduler::decide::{schedule, ScheduleOutcome};
use crate::scheduler::expiry::{evaluate_expiry, ExpiryDecision};
use crate::scheduler::selector::selector_is_empty;
use crate::scheduler::views::{
    ActiveLeaseView, ClientView, ExporterView, LeaseView, PolicyRuleView, PolicyView,
};

/// Reconciler dependencies shared across reconcile passes.
///
/// Unlike the Exporter/Client reconcilers, the Lease reconciler mints no
/// credentials and (matching the Go `LeaseReconciler`) emits no events, so it
/// needs only the kube client.
pub struct Context {
    /// Kube client.
    pub client: Client,
}

/// Errors surfaced from a reconcile pass.
#[derive(Debug, thiserror::Error)]
pub enum LeaseError {
    /// A kube API call failed.
    #[error("kube api error: {0}")]
    Kube(#[from] kube::Error),
    /// `spec.selector` could not be converted into an evaluable selector
    /// (port of the `GetExporterSelector` error path,
    /// go: lease_controller.go:214-216).
    #[error("failed to parse exporter selector: {0}")]
    Selector(kube::core::ParseExpressionError),
    /// The assigned exporter lacks the name/uid needed for a controller owner
    /// reference (port of `SetControllerReference` returning an error,
    /// go: lease_controller.go:112-114).
    #[error("assigned exporter has no name/uid for controller owner reference")]
    OwnerReference,
}

/// Port of `RequeueConflict` (`errors.go`): a 409 conflict requeues immediately
/// with no error and no backoff; anything else propagates.
fn is_conflict(err: &kube::Error) -> bool {
    matches!(err, kube::Error::Api(response) if response.code == 409)
}

/// Reconcile a single `Lease`. Port of `LeaseReconciler.Reconcile`
/// (go: lease_controller.go:69-122).
pub async fn reconcile(lease: Arc<Lease>, ctx: Arc<Context>) -> Result<Action, LeaseError> {
    let namespace = lease.namespace().unwrap_or_default();
    let name = lease.name_any();
    let mut lease = (*lease).clone();

    let now = Timestamp::now();
    let now_dt = to_chrono(now);

    let mut requeue: Option<Duration> = None;

    // 1. reconcileStatusExporterRef — only while unassigned and not ended
    //    (go: lease_controller.go:193-197). All the scheduling reads happen in
    //    exactly this state, matching the Go guards.
    let ended = lease.status.as_ref().is_some_and(|s| s.ended);
    let assigned = lease
        .status
        .as_ref()
        .is_some_and(|s| s.exporter_ref.is_some());
    if !ended && !assigned {
        requeue = schedule_status_exporter_ref(&mut lease, &ctx.client, &namespace, now_dt).await?;
    }

    // 2. reconcileStatusBeginEndTimes (go: lease_controller.go:169-182)
    reconcile_status_begin_end_times(&mut lease, now);

    // 3. reconcileStatusEnded (go: lease_controller.go:126-166)
    if let Some(expiry_requeue) = reconcile_status_ended(&mut lease, now, now_dt) {
        requeue = Some(expiry_requeue);
    }

    // 4. status update; a 409 requeues immediately (go: lease_controller.go:93-95).
    //    A full `replace_status` (PUT with resourceVersion) reproduces Go's
    //    `Status().Update` optimistic concurrency, which is what makes the
    //    RequeueConflict path reachable. The returned object carries the bumped
    //    resourceVersion used for the metadata update below.
    let api: Api<Lease> = Api::namespaced(ctx.client.clone(), &namespace);
    let mut lease = match api
        .replace_status(&name, &PostParams::default(), &lease)
        .await
    {
        Ok(updated) => updated,
        Err(err) if is_conflict(&err) => return Ok(Action::requeue(Duration::ZERO)),
        Err(err) => return Err(err.into()),
    };

    // 5a. lease-ended label (go: lease_controller.go:97-102). Only added; never
    //     removed, and existing labels are preserved.
    if lease.status.as_ref().is_some_and(|s| s.ended) {
        lease
            .metadata
            .labels
            .get_or_insert_with(Default::default)
            .insert(
                LEASE_LABEL_ENDED.to_string(),
                LEASE_LABEL_ENDED_VALUE.to_string(),
            );
    }

    // 5b. controller owner reference from the assigned exporter
    //     (go: lease_controller.go:104-115, controllerutil.SetControllerReference).
    if let Some(exporter_ref) = lease.status.as_ref().and_then(|s| s.exporter_ref.clone()) {
        let exporters: Api<Exporter> = Api::namespaced(ctx.client.clone(), &namespace);
        // Go uses r.Get and returns the raw error (no IgnoreNotFound), so a
        // missing exporter fails the reconcile and retries.
        let exporter = exporters.get(&exporter_ref.name).await?;
        let owner_ref = exporter
            .controller_owner_ref(&())
            .ok_or(LeaseError::OwnerReference)?;
        let refs = lease
            .metadata
            .owner_references
            .get_or_insert_with(Default::default);
        upsert_controller_ref(refs, owner_ref);
    }

    // 5c. metadata update; a 409 requeues immediately (go: lease_controller.go:117-119).
    match api.replace(&name, &PostParams::default(), &lease).await {
        Ok(_) => {}
        Err(err) if is_conflict(&err) => return Ok(Action::requeue(Duration::ZERO)),
        Err(err) => return Err(err.into()),
    }

    Ok(requeue.map_or_else(Action::await_change, Action::requeue))
}

/// Gather the scheduler views and run [`schedule`], applying the resulting
/// status mutation to `lease` and returning any scheduling requeue
/// (`WaitUntilBegin`/`Pending`). Port of the read+decide half of
/// `reconcileStatusExporterRef` (go: lease_controller.go:185-361).
async fn schedule_status_exporter_ref(
    lease: &mut Lease,
    client: &Client,
    namespace: &str,
    now_dt: DateTime<Utc>,
) -> Result<Option<Duration>, LeaseError> {
    let exporters_api: Api<Exporter> = Api::namespaced(client.clone(), namespace);

    // Candidate exporters: a pinned exporter is fetched by name (unfiltered, so
    // a selector mismatch still surfaces as SelectorMismatch inside the
    // scheduler); otherwise the selector filters server-side. An empty selector
    // with no pin lists nothing — the scheduler returns InvalidSelector — so we
    // skip a would-be match-everything list (go: lease_controller.go:222-256).
    let exporters: Vec<Exporter> = if let Some(pinned) = lease.spec.exporter_ref.as_ref() {
        match exporters_api.get_opt(&pinned.name).await? {
            Some(exporter) => vec![exporter],
            None => Vec::new(),
        }
    } else if selector_is_empty(&lease.spec.selector) {
        Vec::new()
    } else {
        let selector = lease
            .get_exporter_selector()
            .map_err(LeaseError::Selector)?;
        exporters_api
            .list(&ListParams::default().labels_from(&selector))
            .await?
            .items
    };

    // Policies drive approval; an empty list means approve-all (and the client
    // is never fetched, matching Go's optimization at lease_controller.go:377-396).
    let policies_api: Api<ExporterAccessPolicy> = Api::namespaced(client.clone(), namespace);
    let policies = policies_api.list(&ListParams::default()).await?.items;

    let client_view = if policies.is_empty() {
        ClientView::default()
    } else {
        let clients_api: Api<ClientCr> = Api::namespaced(client.clone(), namespace);
        let jclient = clients_api.get(&lease.spec.client_ref.name).await?;
        build_client_view(&jclient)
    };

    // Active (not-ended) leases mark already-held exporters.
    let leases_api: Api<Lease> = Api::namespaced(client.clone(), namespace);
    let active = leases_api
        .list(&ListParams::default().labels(&format!("!{LEASE_LABEL_ENDED}")))
        .await?
        .items;

    let exporter_views: Vec<ExporterView> = exporters.iter().map(build_exporter_view).collect();
    let policy_views: Vec<PolicyView> = policies.iter().map(build_policy_view).collect();
    let active_views: Vec<ActiveLeaseView> = active.iter().map(build_active_lease_view).collect();
    let lease_view = build_lease_view(lease);

    let outcome = schedule(
        &lease_view,
        &exporter_views,
        &policy_views,
        &client_view,
        &active_views,
        now_dt,
    );
    Ok(apply_schedule_outcome(lease, outcome))
}

/// Apply a [`ScheduleOutcome`] to the lease status, returning the scheduling
/// requeue (if any). The condition mutations go through the `Lease`'s own
/// `set_status_*` helpers, which are exact ports of the Go `SetStatus*` methods
/// (`meta.SetStatusCondition` semantics).
fn apply_schedule_outcome(lease: &mut Lease, outcome: ScheduleOutcome) -> Option<Duration> {
    match outcome {
        // Not produced for an unassigned lease, but the identity outcome.
        ScheduleOutcome::NoChange => None,
        // go: lease_controller.go:199-210 (scheduled for the future)
        ScheduleOutcome::WaitUntilBegin(wait) => Some(wait),
        // go: lease_controller.go:218 (SetStatusInvalid)
        ScheduleOutcome::Invalid { reason, message } => {
            lease.set_status_invalid(reason, message);
            None
        }
        // go: lease_controller.go:230/240/269/304 (SetStatusUnsatisfiable)
        ScheduleOutcome::Unsatisfiable { reason, message } => {
            lease.set_status_unsatisfiable(reason, message);
            None
        }
        // go: lease_controller.go:284/312/323/345 (SetStatusPending + 1s requeue)
        ScheduleOutcome::Pending {
            reason,
            message,
            requeue,
        } => {
            lease.set_status_pending(reason, message);
            Some(requeue)
        }
        // go: lease_controller.go:352-356 (assign exporterRef/priority/spotAccess)
        ScheduleOutcome::Assign {
            exporter,
            priority,
            spot,
        } => {
            let status = lease.status.get_or_insert_with(Default::default);
            status.priority = priority;
            status.spot_access = spot;
            status.exporter_ref = Some(LocalObjectReference { name: exporter });
            None
        }
    }
}

/// Port of `reconcileStatusBeginEndTimes` (go: lease_controller.go:169-182):
/// once an exporter is acquired, stamp the actual `status.beginTime` and set the
/// `Ready` condition (only on the first pass, i.e. while `beginTime` is unset).
fn reconcile_status_begin_end_times(lease: &mut Lease, now: Timestamp) {
    let needs_begin = lease
        .status
        .as_ref()
        .is_some_and(|s| s.begin_time.is_none() && s.exporter_ref.is_some());
    if needs_begin {
        lease.status.get_or_insert_with(Default::default).begin_time = Some(Time(now));
        lease.set_status_ready(
            true,
            "Ready",
            "An exporter has been acquired for the client",
        );
    }
}

/// Port of `reconcileStatusEnded` (go: lease_controller.go:126-166): decide
/// whether the lease ends this pass and, if it stays active, when to requeue for
/// expiry. Returns the expiry requeue duration (if the lease is scheduled to
/// expire in the future).
fn reconcile_status_ended(
    lease: &mut Lease,
    now: Timestamp,
    now_dt: DateTime<Utc>,
) -> Option<Duration> {
    // Already ended: nothing to do (go: lease_controller.go:133).
    if lease.status.as_ref().is_some_and(|s| s.ended) {
        return None;
    }

    // Unsatisfiable/Invalid conditions terminate the lease so it is not
    // reprocessed (go: lease_controller.go:135-139).
    let (unsatisfiable, invalid) = {
        let conditions: &[_] = lease
            .status
            .as_ref()
            .map_or(&[], |s| s.conditions.as_slice());
        (
            is_status_condition_true(conditions, LEASE_CONDITION_TYPE_UNSATISFIABLE),
            is_status_condition_true(conditions, LEASE_CONDITION_TYPE_INVALID),
        )
    };
    if unsatisfiable || invalid {
        let status = lease.status.get_or_insert_with(Default::default);
        status.ended = true;
        status.end_time = Some(Time(now));
        return None;
    }

    // Explicit release request (go: lease_controller.go:140-142).
    if lease.spec.release {
        lease.release();
        return None;
    }

    // Expiry is only evaluated once the lease has actually begun
    // (go: lease_controller.go:143). `evaluate_expiry` reproduces the Go
    // precedence and the zero-time-immediate-expire quirk.
    let status_begin = lease.status.as_ref().and_then(|s| s.begin_time.clone())?;
    let decision = evaluate_expiry(
        lease.spec.begin_time.as_ref().map(|t| to_chrono(t.0)),
        lease.spec.end_time.as_ref().map(|t| to_chrono(t.0)),
        lease.spec.duration,
        to_chrono(status_begin.0),
        now_dt,
    );
    match decision {
        ExpiryDecision::Expire => {
            lease.expire();
            None
        }
        // go: lease_controller.go:160 (result.RequeueAfter = expiration.Sub(now))
        ExpiryDecision::RequeueAt(expiration) => {
            Some((expiration - now_dt).to_std().unwrap_or(Duration::ZERO))
        }
    }
}

// ---------------------------------------------------------------------------
// View builders: project the kube CRs into the scheduler's cluster-free views.
// ---------------------------------------------------------------------------

/// Project an `Exporter` into an [`ExporterView`], pre-deriving `online` /
/// `registered` from its status conditions (`meta.IsStatusConditionTrue`) and
/// defaulting an unset `exporterStatus` to `Unspecified` (which the readiness
/// filter treats as the empty Go value).
fn build_exporter_view(exporter: &Exporter) -> ExporterView {
    let conditions: &[_] = exporter
        .status
        .as_ref()
        .and_then(|s| s.conditions.as_deref())
        .unwrap_or(&[]);
    ExporterView {
        name: exporter.name_any(),
        labels: exporter.metadata.labels.clone().unwrap_or_default(),
        online: is_status_condition_true(conditions, EXPORTER_CONDITION_TYPE_ONLINE),
        registered: is_status_condition_true(conditions, EXPORTER_CONDITION_TYPE_REGISTERED),
        exporter_status: exporter
            .status
            .as_ref()
            .and_then(|s| s.exporter_status)
            .unwrap_or(ExporterStatusValue::Unspecified),
    }
}

/// Project an `ExporterAccessPolicy` into a [`PolicyView`].
fn build_policy_view(policy: &ExporterAccessPolicy) -> PolicyView {
    PolicyView {
        exporter_selector: policy.spec.exporter_selector.clone(),
        rules: policy
            .spec
            .policies
            .iter()
            .map(|rule| PolicyRuleView {
                from: rule
                    .from
                    .iter()
                    .map(|from| from.client_selector.clone())
                    .collect(),
                priority: rule.priority,
                spot_access: rule.spot_access,
                maximum_duration: rule.maximum_duration,
                description: rule.description.clone(),
            })
            .collect(),
    }
}

/// Project an active `Lease` into an [`ActiveLeaseView`].
fn build_active_lease_view(lease: &Lease) -> ActiveLeaseView {
    let status = lease.status.as_ref();
    ActiveLeaseView {
        exporter_ref: status
            .and_then(|s| s.exporter_ref.as_ref())
            .map(|r| r.name.clone()),
        spot_access: status.is_some_and(|s| s.spot_access),
    }
}

/// Project the `Client` into a [`ClientView`] (only its labels matter, for
/// policy `from.clientSelector` matching).
fn build_client_view(client: &ClientCr) -> ClientView {
    ClientView {
        labels: client.metadata.labels.clone().unwrap_or_default(),
    }
}

/// Project the lease under reconciliation into a [`LeaseView`].
fn build_lease_view(lease: &Lease) -> LeaseView {
    LeaseView {
        selector: lease.spec.selector.clone(),
        exporter_ref: lease.spec.exporter_ref.as_ref().map(|r| r.name.clone()),
        begin_time: lease.spec.begin_time.as_ref().map(|t| to_chrono(t.0)),
        end_time: lease.spec.end_time.as_ref().map(|t| to_chrono(t.0)),
        duration: lease.spec.duration,
    }
}

/// Convert a k8s (`jiff`) [`Timestamp`] into a `chrono` `DateTime<Utc>`, the
/// clock type the scheduler core speaks. Real lease timestamps are positive, so
/// the second + nanosecond split is exact; a negative sub-second (only the Go
/// zero-time sentinel, whose sub-second is 0) is clamped defensively.
fn to_chrono(ts: Timestamp) -> DateTime<Utc> {
    DateTime::from_timestamp(ts.as_second(), ts.subsec_nanosecond().max(0) as u32)
        .unwrap_or_default()
}

/// Emulate `controllerutil.SetControllerReference`'s upsert: replace any
/// existing owner reference with the same UID, otherwise append. Preserves any
/// other (non-controller) owner references already on the lease.
fn upsert_controller_ref(refs: &mut Vec<OwnerReference>, owner_ref: OwnerReference) {
    if let Some(slot) = refs.iter_mut().find(|r| r.uid == owner_ref.uid) {
        *slot = owner_ref;
    } else {
        refs.push(owner_ref);
    }
}

/// Requeue policy on reconcile error: short backoff (matches the Exporter and
/// Client reconcilers; controller-runtime's default is also a bounded backoff).
fn error_policy(_lease: Arc<Lease>, _err: &LeaseError, _ctx: Arc<Context>) -> Action {
    Action::requeue(Duration::from_secs(5))
}

/// Build and run the Lease controller: `For(Lease)` only — no `Owns`/`Watches`
/// (go: `LeaseReconciler.SetupWithManager`, lease_controller.go:589-593). Runs
/// until the stream ends.
pub async fn run(client: Client, namespace: String) {
    let context = Arc::new(Context {
        client: client.clone(),
    });

    Controller::new(
        Api::<Lease>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .run(reconcile, error_policy, context)
    .for_each(|_| async {})
    .await;
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use jumpstarter_controller_api::access_policy::{
        ExporterAccessPolicySpec, From as PolicyFrom, Policy,
    };
    use jumpstarter_controller_api::client::ClientSpec;
    use jumpstarter_controller_api::conditions::{
        LEASE_CONDITION_TYPE_INVALID, LEASE_CONDITION_TYPE_PENDING, LEASE_CONDITION_TYPE_READY,
        LEASE_CONDITION_TYPE_UNSATISFIABLE,
    };
    use jumpstarter_controller_api::exporter::{ExporterSpec, ExporterStatus};
    use jumpstarter_controller_api::go_duration::{GoDuration, SECOND};
    use jumpstarter_controller_api::lease::{LeaseSpec, LeaseStatus};
    use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, LabelSelector, ObjectMeta};

    use super::*;

    fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    fn ts(secs: i64) -> Timestamp {
        Timestamp::from_second(secs).unwrap()
    }

    fn true_condition(type_: &str) -> Condition {
        Condition {
            type_: type_.to_string(),
            status: "True".to_string(),
            observed_generation: None,
            last_transition_time: Time(ts(1_700_000_000)),
            reason: "x".to_string(),
            message: String::new(),
        }
    }

    fn base_lease() -> Lease {
        let mut lease = Lease::new(
            "lease-1",
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: "client-1".into(),
                },
                duration: Some(GoDuration(2 * SECOND)),
                selector: LabelSelector {
                    match_labels: Some(labels(&[("dut", "a")])),
                    match_expressions: None,
                },
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some("default".into());
        lease.metadata.generation = Some(1);
        lease
    }

    // -- view builders -------------------------------------------------------

    #[test]
    fn build_exporter_view_derives_online_registered_and_status() {
        let exporter = Exporter {
            metadata: ObjectMeta {
                name: Some("exporter-1".into()),
                labels: Some(labels(&[("dut", "a")])),
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: Some(ExporterStatus {
                conditions: Some(vec![
                    true_condition(EXPORTER_CONDITION_TYPE_ONLINE),
                    true_condition(EXPORTER_CONDITION_TYPE_REGISTERED),
                ]),
                exporter_status: Some(ExporterStatusValue::Available),
                ..Default::default()
            }),
        };
        let view = build_exporter_view(&exporter);
        assert_eq!(view.name, "exporter-1");
        assert_eq!(view.labels, labels(&[("dut", "a")]));
        assert!(view.online);
        assert!(view.registered);
        assert_eq!(view.exporter_status, ExporterStatusValue::Available);
    }

    #[test]
    fn build_exporter_view_defaults_when_status_absent() {
        let exporter = Exporter {
            metadata: ObjectMeta {
                name: Some("exporter-2".into()),
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: None,
        };
        let view = build_exporter_view(&exporter);
        assert!(!view.online);
        assert!(!view.registered);
        assert!(view.labels.is_empty());
        // Unset exporterStatus is treated as Unspecified (ready for backwards compat).
        assert_eq!(view.exporter_status, ExporterStatusValue::Unspecified);
    }

    #[test]
    fn build_policy_view_maps_rules() {
        let policy = ExporterAccessPolicy::new(
            "policy-1",
            ExporterAccessPolicySpec {
                exporter_selector: LabelSelector {
                    match_labels: Some(labels(&[("dut", "a")])),
                    match_expressions: None,
                },
                policies: vec![Policy {
                    description: "admins only".into(),
                    priority: 10,
                    spot_access: true,
                    maximum_duration: Some(GoDuration(3600 * SECOND)),
                    from: vec![PolicyFrom {
                        client_selector: LabelSelector {
                            match_labels: Some(labels(&[("role", "admin")])),
                            match_expressions: None,
                        },
                    }],
                }],
            },
        );
        let view = build_policy_view(&policy);
        assert_eq!(
            view.exporter_selector.match_labels,
            Some(labels(&[("dut", "a")]))
        );
        assert_eq!(view.rules.len(), 1);
        let rule = &view.rules[0];
        assert_eq!(rule.priority, 10);
        assert!(rule.spot_access);
        assert_eq!(rule.maximum_duration, Some(GoDuration(3600 * SECOND)));
        assert_eq!(rule.description, "admins only");
        assert_eq!(rule.from.len(), 1);
        assert_eq!(
            rule.from[0].match_labels,
            Some(labels(&[("role", "admin")]))
        );
    }

    #[test]
    fn build_active_lease_view_reads_status() {
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            exporter_ref: Some(LocalObjectReference {
                name: "exporter-9".into(),
            }),
            spot_access: true,
            ..Default::default()
        });
        let view = build_active_lease_view(&lease);
        assert_eq!(view.exporter_ref.as_deref(), Some("exporter-9"));
        assert!(view.spot_access);

        // No status ⇒ no exporter, no spot.
        let bare = base_lease();
        let view = build_active_lease_view(&bare);
        assert_eq!(view.exporter_ref, None);
        assert!(!view.spot_access);
    }

    #[test]
    fn build_client_view_reads_labels() {
        let client = ClientCr {
            metadata: ObjectMeta {
                name: Some("client-1".into()),
                labels: Some(labels(&[("role", "admin")])),
                ..Default::default()
            },
            spec: ClientSpec::default(),
            status: None,
        };
        let view = build_client_view(&client);
        assert_eq!(view.labels, labels(&[("role", "admin")]));
    }

    #[test]
    fn build_lease_view_projects_spec() {
        let mut lease = base_lease();
        lease.spec.exporter_ref = Some(LocalObjectReference {
            name: "pinned".into(),
        });
        lease.spec.begin_time = Some(Time(ts(1_700_000_100)));
        let view = build_lease_view(&lease);
        assert_eq!(view.exporter_ref.as_deref(), Some("pinned"));
        assert_eq!(view.duration, Some(GoDuration(2 * SECOND)));
        assert_eq!(view.selector.match_labels, Some(labels(&[("dut", "a")])));
        assert_eq!(view.begin_time, Some(to_chrono(ts(1_700_000_100))));
        assert_eq!(view.end_time, None);
    }

    // -- apply_schedule_outcome ----------------------------------------------

    fn lease_condition<'a>(lease: &'a Lease, type_: &str) -> Option<&'a Condition> {
        lease
            .status
            .as_ref()?
            .conditions
            .iter()
            .find(|c| c.type_ == type_)
    }

    #[test]
    fn apply_assign_sets_exporter_priority_and_spot() {
        let mut lease = base_lease();
        let requeue = apply_schedule_outcome(
            &mut lease,
            ScheduleOutcome::Assign {
                exporter: "exporter-1".into(),
                priority: 7,
                spot: true,
            },
        );
        assert_eq!(requeue, None);
        let status = lease.status.as_ref().unwrap();
        assert_eq!(status.exporter_ref.as_ref().unwrap().name, "exporter-1");
        assert_eq!(status.priority, 7);
        assert!(status.spot_access);
    }

    #[test]
    fn apply_pending_sets_condition_and_one_second_requeue() {
        let mut lease = base_lease();
        let requeue = apply_schedule_outcome(
            &mut lease,
            ScheduleOutcome::Pending {
                reason: "Offline",
                message: "none online".into(),
                requeue: Duration::from_secs(1),
            },
        );
        assert_eq!(requeue, Some(Duration::from_secs(1)));
        let cond = lease_condition(&lease, LEASE_CONDITION_TYPE_PENDING).unwrap();
        assert_eq!(cond.status, "True");
        assert_eq!(cond.reason, "Offline");
        assert_eq!(cond.message, "none online");
    }

    #[test]
    fn apply_invalid_and_unsatisfiable_set_conditions_without_requeue() {
        let mut lease = base_lease();
        let requeue = apply_schedule_outcome(
            &mut lease,
            ScheduleOutcome::Invalid {
                reason: "InvalidSelector",
                message: "empty".into(),
            },
        );
        assert_eq!(requeue, None);
        assert_eq!(
            lease_condition(&lease, LEASE_CONDITION_TYPE_INVALID)
                .unwrap()
                .status,
            "True"
        );

        let mut lease = base_lease();
        let requeue = apply_schedule_outcome(
            &mut lease,
            ScheduleOutcome::Unsatisfiable {
                reason: "NoAccess",
                message: "denied".into(),
            },
        );
        assert_eq!(requeue, None);
        assert_eq!(
            lease_condition(&lease, LEASE_CONDITION_TYPE_UNSATISFIABLE)
                .unwrap()
                .status,
            "True"
        );
    }

    #[test]
    fn apply_wait_until_begin_returns_wait_without_mutation() {
        let mut lease = base_lease();
        let requeue = apply_schedule_outcome(
            &mut lease,
            ScheduleOutcome::WaitUntilBegin(Duration::from_secs(5)),
        );
        assert_eq!(requeue, Some(Duration::from_secs(5)));
        // No exporter assigned, no conditions written.
        assert!(lease.status.is_none() || lease.status.as_ref().unwrap().exporter_ref.is_none());
    }

    // -- reconcile_status_begin_end_times ------------------------------------

    #[test]
    fn begin_end_times_stamps_begin_and_ready_once_acquired() {
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            exporter_ref: Some(LocalObjectReference {
                name: "exporter-1".into(),
            }),
            ..Default::default()
        });
        reconcile_status_begin_end_times(&mut lease, ts(1_700_000_500));
        let status = lease.status.as_ref().unwrap();
        assert_eq!(status.begin_time, Some(Time(ts(1_700_000_500))));
        let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).unwrap();
        assert_eq!(ready.status, "True");
        assert_eq!(ready.reason, "Ready");
        assert_eq!(
            ready.message,
            "An exporter has been acquired for the client"
        );
    }

    #[test]
    fn begin_end_times_noop_without_exporter_or_when_already_begun() {
        // No exporter ⇒ nothing happens.
        let mut lease = base_lease();
        reconcile_status_begin_end_times(&mut lease, ts(1_700_000_500));
        assert!(lease.status.is_none() || lease.status.as_ref().unwrap().begin_time.is_none());

        // Already begun ⇒ begin_time preserved, no new Ready written.
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            exporter_ref: Some(LocalObjectReference {
                name: "exporter-1".into(),
            }),
            begin_time: Some(Time(ts(1_700_000_000))),
            ..Default::default()
        });
        reconcile_status_begin_end_times(&mut lease, ts(1_700_000_500));
        assert_eq!(
            lease.status.as_ref().unwrap().begin_time,
            Some(Time(ts(1_700_000_000)))
        );
        assert!(lease_condition(&lease, LEASE_CONDITION_TYPE_READY).is_none());
    }

    // -- reconcile_status_ended ----------------------------------------------

    #[test]
    fn ended_marks_terminated_on_unsatisfiable_condition() {
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            conditions: vec![true_condition(LEASE_CONDITION_TYPE_UNSATISFIABLE)],
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_000), to_chrono(ts(1_700_000_000)));
        assert_eq!(requeue, None);
        let status = lease.status.as_ref().unwrap();
        assert!(status.ended);
        assert_eq!(status.end_time, Some(Time(ts(1_700_000_000))));
    }

    #[test]
    fn ended_marks_terminated_on_invalid_condition() {
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            conditions: vec![true_condition(LEASE_CONDITION_TYPE_INVALID)],
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_000), to_chrono(ts(1_700_000_000)));
        assert_eq!(requeue, None);
        assert!(lease.status.as_ref().unwrap().ended);
    }

    #[test]
    fn ended_honors_release_flag() {
        let mut lease = base_lease();
        lease.spec.release = true;
        lease.status = Some(LeaseStatus {
            begin_time: Some(Time(ts(1_700_000_000))),
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_001), to_chrono(ts(1_700_000_001)));
        assert_eq!(requeue, None);
        let status = lease.status.as_ref().unwrap();
        assert!(status.ended);
        let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).unwrap();
        assert_eq!(ready.reason, "Released");
    }

    #[test]
    fn ended_requeues_for_future_expiry() {
        // begun at t0, duration 10s ⇒ expiry at t0+10; now = t0+2 ⇒ requeue ~8s.
        let mut lease = base_lease();
        lease.spec.duration = Some(GoDuration(10 * SECOND));
        lease.status = Some(LeaseStatus {
            begin_time: Some(Time(ts(1_700_000_000))),
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_002), to_chrono(ts(1_700_000_002)));
        assert_eq!(requeue, Some(Duration::from_secs(8)));
        assert!(!lease.status.as_ref().unwrap().ended);
    }

    #[test]
    fn ended_expires_immediately_when_past_due() {
        let mut lease = base_lease();
        lease.spec.duration = Some(GoDuration(SECOND));
        lease.status = Some(LeaseStatus {
            begin_time: Some(Time(ts(1_700_000_000))),
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_100), to_chrono(ts(1_700_000_100)));
        assert_eq!(requeue, None);
        let status = lease.status.as_ref().unwrap();
        assert!(status.ended);
        let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).unwrap();
        assert_eq!(ready.reason, "Expired");
    }

    #[test]
    fn ended_is_noop_when_already_ended() {
        let mut lease = base_lease();
        lease.status = Some(LeaseStatus {
            ended: true,
            begin_time: Some(Time(ts(1_700_000_000))),
            // An unsatisfiable condition would otherwise re-stamp end_time; the
            // already-ended guard must short-circuit first.
            conditions: vec![true_condition(LEASE_CONDITION_TYPE_UNSATISFIABLE)],
            end_time: Some(Time(ts(1_700_000_050))),
            ..Default::default()
        });
        let requeue =
            reconcile_status_ended(&mut lease, ts(1_700_000_100), to_chrono(ts(1_700_000_100)));
        assert_eq!(requeue, None);
        // end_time untouched.
        assert_eq!(
            lease.status.as_ref().unwrap().end_time,
            Some(Time(ts(1_700_000_050)))
        );
    }

    // -- requeue → Action mapping --------------------------------------------

    #[test]
    fn requeue_option_maps_to_action() {
        let none: Option<Duration> = None;
        assert_eq!(
            none.map_or_else(Action::await_change, Action::requeue),
            Action::await_change()
        );
        let some = Some(Duration::from_secs(1));
        assert_eq!(
            some.map_or_else(Action::await_change, Action::requeue),
            Action::requeue(Duration::from_secs(1))
        );
        // The immediate-conflict requeue is a zero-duration requeue, distinct
        // from await_change.
        assert_ne!(Action::requeue(Duration::ZERO), Action::await_change());
    }

    // -- upsert_controller_ref -----------------------------------------------

    #[test]
    fn upsert_controller_ref_replaces_by_uid_else_appends() {
        let mut refs = vec![OwnerReference {
            api_version: "v1".into(),
            kind: "Other".into(),
            name: "other".into(),
            uid: "uid-other".into(),
            ..Default::default()
        }];
        let owner = OwnerReference {
            api_version: "jumpstarter.dev/v1alpha1".into(),
            kind: "Exporter".into(),
            name: "exporter-1".into(),
            uid: "uid-1".into(),
            controller: Some(true),
            block_owner_deletion: Some(true),
        };
        upsert_controller_ref(&mut refs, owner.clone());
        assert_eq!(refs.len(), 2);
        assert_eq!(refs[1], owner);

        // Same uid, renamed ⇒ replace in place, preserve the other.
        let renamed = OwnerReference {
            name: "exporter-1-renamed".into(),
            ..owner.clone()
        };
        upsert_controller_ref(&mut refs, renamed.clone());
        assert_eq!(refs.len(), 2);
        assert_eq!(refs[1], renamed);
        assert_eq!(refs[0].uid, "uid-other");
    }
}
