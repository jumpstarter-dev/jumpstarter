//! The Exporter reconciler, porting
//! `controller/internal/controller/exporter_controller.go`.
//!
//! Per reconcile pass (level-triggered), for an `Exporter`:
//!
//!   1. ensure the credential `Secret` `<name>-exporter` (owner-referenced);
//!   2. set `status.leaseRef` from the namespace's active leases;
//!   3. compute the `Online` / `Registered` conditions and the reported
//!      `exporterStatus` (offline when `lastSeen` is older than one minute, with
//!      the graceful-shutdown special case);
//!   4. set `status.endpoint = controllerEndpoint()`;
//!   5. patch the status subresource, then emit registration/online events
//!      **only after** the patch succeeds;
//!   6. requeue after 30s while online (rely on watches otherwise); a 409
//!      conflict requeues immediately with no error.
//!
//! No finalizer — the credential `Secret` and the `Owns(Lease)` children are
//! garbage-collected via owner references.
//!
//! The condition/offline computation ([`reconcile_status_conditions_online`])
//! and the event decision ([`exporter_events`]) are pure and cluster-free so
//! they are table-testable.

use std::sync::Arc;
use std::time::Duration;

use futures::StreamExt;
use jumpstarter_controller_api::conditions::{
    EXPORTER_CONDITION_TYPE_ONLINE, EXPORTER_CONDITION_TYPE_REGISTERED,
};
use jumpstarter_controller_api::exporter::{Exporter, ExporterStatus, ExporterStatusValue};
use jumpstarter_controller_api::labels::LEASE_LABEL_ENDED;
use jumpstarter_controller_api::lease::Lease;
use jumpstarter_controller_auth::signer::Signer;
use k8s_openapi::api::core::v1::{LocalObjectReference, Secret};
use k8s_openapi::jiff::{SignedDuration, Timestamp};
use kube::api::{Api, ListParams, Patch, PatchParams};
use kube::runtime::controller::{Action, Controller};
use kube::runtime::events::{Event, EventType, Recorder, Reporter};
use kube::runtime::watcher;
use kube::{Client, Resource, ResourceExt};

use crate::conditions::{condition, is_status_condition_true, is_zero_time, set_status_condition};
use crate::secret::{ensure_secret, SecretError};

/// Reconciler dependencies shared across reconcile passes.
pub struct Context {
    /// Kube client.
    pub client: Client,
    /// The internal ES256 signer minting credential tokens.
    pub signer: Arc<Signer>,
    /// Event recorder (`events.k8s.io`).
    pub recorder: Recorder,
}

/// Errors surfaced from a reconcile pass.
#[derive(Debug, thiserror::Error)]
pub enum ExporterError {
    /// A kube API call failed.
    #[error("kube api error: {0}")]
    Kube(#[from] kube::Error),
    /// Credential-secret reconciliation failed.
    #[error(transparent)]
    Secret(#[from] SecretError),
}

/// The controller gRPC endpoint advertised to exporters/clients.
///
/// Port of `controllerEndpoint()` (`endpoints.go`): `GRPC_ENDPOINT` if set and
/// non-empty, else `localhost:8082`.
pub fn controller_endpoint() -> String {
    match std::env::var("GRPC_ENDPOINT") {
        Ok(endpoint) if !endpoint.is_empty() => endpoint,
        _ => "localhost:8082".to_string(),
    }
}

/// Pure port of `reconcileStatusConditionsOnline`
/// (`exporter_controller.go:191-271`): mutates the exporter's `Online` /
/// `Registered` conditions, `exporterStatus` and `statusMessage` in place and
/// returns the requeue delay (30s while online, otherwise zero — meaning "rely
/// on watches", mapped to [`Action::await_change`] by the caller).
///
/// `now` is injected so the offline threshold is deterministic in tests.
pub fn reconcile_status_conditions_online(exporter: &mut Exporter, now: Timestamp) -> Duration {
    let generation = exporter.meta().generation.unwrap_or(0);
    let status = exporter.status.get_or_insert_with(ExporterStatus::default);
    if status.conditions.is_none() {
        status.conditions = Some(Vec::new());
    }

    // `LastSeen.IsZero()` — absent or the Go zero time both count as "never
    // seen". Copy the timestamp out so no borrow is held while other status
    // fields are mutated.
    let last_seen: Option<Timestamp> = status
        .last_seen
        .as_ref()
        .filter(|t| !is_zero_time(t))
        .map(|t| t.0);

    let mut requeue = Duration::ZERO;
    let one_minute = SignedDuration::from_mins(1);

    match last_seen {
        // go: exporter_controller.go:197-212 (LastSeen zero — never seen)
        None => {
            set_status_condition(
                status.conditions.as_mut().unwrap(),
                condition(
                    EXPORTER_CONDITION_TYPE_ONLINE,
                    false,
                    generation,
                    "Seen",
                    "Never seen",
                ),
                now,
            );
            // Deliberately do NOT set exporterStatus to Offline here (v0.7.x
            // exporters never ReportStatus; see the Go comment).
            status.status_message = Some("Never seen".to_string());
        }
        // go: exporter_controller.go:213-226 (last seen > 1 minute ago)
        Some(seen) if now.duration_since(seen) > one_minute => {
            set_status_condition(
                status.conditions.as_mut().unwrap(),
                condition(
                    EXPORTER_CONDITION_TYPE_ONLINE,
                    false,
                    generation,
                    "Seen",
                    "Last seen more than 1 minute ago",
                ),
                now,
            );
            if status.exporter_status != Some(ExporterStatusValue::Offline) {
                status.exporter_status = Some(ExporterStatusValue::Offline);
                status.status_message =
                    Some("Connection lost - last seen more than 1 minute ago".to_string());
            }
        }
        // go: exporter_controller.go:227-250 (seen recently)
        Some(_) => {
            if status.exporter_status == Some(ExporterStatusValue::Offline) {
                // Reported offline despite a recent LastSeen: graceful shutdown.
                let message = status.status_message.clone().unwrap_or_default();
                set_status_condition(
                    status.conditions.as_mut().unwrap(),
                    condition(
                        EXPORTER_CONDITION_TYPE_ONLINE,
                        false,
                        generation,
                        "Offline",
                        &message,
                    ),
                    now,
                );
            } else {
                set_status_condition(
                    status.conditions.as_mut().unwrap(),
                    condition(
                        EXPORTER_CONDITION_TYPE_ONLINE,
                        true,
                        generation,
                        "Seen",
                        "Last seen less than 1 minute ago",
                    ),
                    now,
                );
                requeue = Duration::from_secs(30);
            }
        }
    }

    // go: exporter_controller.go:252-266 (Registered condition)
    let registered = status.devices.is_some();
    let registered_condition = if registered {
        condition(
            EXPORTER_CONDITION_TYPE_REGISTERED,
            true,
            generation,
            "Register",
            "",
        )
    } else {
        condition(
            EXPORTER_CONDITION_TYPE_REGISTERED,
            false,
            generation,
            "Unregister",
            "",
        )
    };
    set_status_condition(
        status.conditions.as_mut().unwrap(),
        registered_condition,
        now,
    );

    requeue
}

/// An event to publish after a successful status patch.
#[derive(Debug, Clone)]
pub struct PendingEvent {
    /// Normal/Warning severity.
    pub type_: EventType,
    /// PascalCase reason (also used as the event `action`).
    pub reason: &'static str,
    /// Human-readable message.
    pub note: String,
}

/// Pure port of the event decisions in `Reconcile`
/// (`exporter_controller.go:100-124`): given the online/registered condition
/// values before and after this pass, produce the events to emit.
pub fn exporter_events(
    exporter: &Exporter,
    prev_online: bool,
    prev_registered: bool,
    new_online: bool,
    new_registered: bool,
    now: Timestamp,
) -> Vec<PendingEvent> {
    let mut events = Vec::new();
    let name = exporter.name_any();
    let status = exporter.status.as_ref();

    // Registration transitions.
    if !prev_registered && new_registered {
        let device_count = status
            .and_then(|s| s.devices.as_ref())
            .map_or(0, |devices| devices.len());
        events.push(PendingEvent {
            type_: EventType::Normal,
            reason: "ExporterRegistered",
            note: format!("Exporter registered its capabilities: deviceCount={device_count}"),
        });
    } else if prev_registered && !new_registered {
        events.push(PendingEvent {
            type_: EventType::Warning,
            reason: "ExporterUnregistered",
            note: format!("Exporter lost its device registration: exporter={name}"),
        });
    }

    // Online transitions.
    if !prev_online && new_online {
        events.push(PendingEvent {
            type_: EventType::Normal,
            reason: "ExporterOnline",
            note: format!(
                "Exporter is online: exporter={name} lastSeen={}",
                fmt_last_seen(status)
            ),
        });
    } else if prev_online && !new_online {
        // Graceful shutdown iff explicitly Offline AND seen within the minute.
        let graceful = status.is_some_and(|s| {
            s.exporter_status == Some(ExporterStatusValue::Offline)
                && s.last_seen
                    .as_ref()
                    .is_some_and(|t| now.duration_since(t.0) <= SignedDuration::from_mins(1))
        });
        if graceful {
            let message = status
                .and_then(|s| s.status_message.clone())
                .unwrap_or_default();
            events.push(PendingEvent {
                type_: EventType::Warning,
                reason: "ExporterOffline",
                note: format!(
                    "Exporter reported offline (graceful shutdown): exporter={name} message={message}"
                ),
            });
        } else {
            events.push(PendingEvent {
                type_: EventType::Warning,
                reason: "ExporterOffline",
                note: format!(
                    "Exporter went offline (connection lost): exporter={name} lastSeen={}",
                    fmt_last_seen(status)
                ),
            });
        }
    }

    events
}

/// Format `status.lastSeen` for event messages. Cosmetic only (events are not
/// a wire-parity surface); Go uses `metav1.Time.String()` — see the module
/// concern note on the format divergence.
fn fmt_last_seen(status: Option<&ExporterStatus>) -> String {
    match status.and_then(|s| s.last_seen.as_ref()) {
        Some(time) => time.0.to_string(),
        None => "0001-01-01 00:00:00 +0000 UTC".to_string(),
    }
}

fn online(exporter: &Exporter) -> bool {
    exporter.status.as_ref().is_some_and(|s| {
        is_status_condition_true(
            s.conditions.as_deref().unwrap_or(&[]),
            EXPORTER_CONDITION_TYPE_ONLINE,
        )
    })
}

fn registered(exporter: &Exporter) -> bool {
    exporter.status.as_ref().is_some_and(|s| {
        is_status_condition_true(
            s.conditions.as_deref().unwrap_or(&[]),
            EXPORTER_CONDITION_TYPE_REGISTERED,
        )
    })
}

/// Port of `RequeueConflict` (`errors.go`): a 409 conflict requeues immediately
/// with no error; anything else propagates.
fn is_conflict(err: &kube::Error) -> bool {
    matches!(err, kube::Error::Api(response) if response.code == 409)
}

/// The RFC-7386 (JSON Merge Patch) delta that turns `original` into `modified`.
///
/// Faithful port of `jsonpatch.CreateMergePatch` / `getDiff`
/// (`github.com/evanphx/json-patch`, the body of controller-runtime's
/// `client.MergeFrom`). Only keys whose value actually changed are emitted:
///
///   * a key present in `original` but absent from `modified` becomes an
///     explicit `null` (a *clear*);
///   * a key added in `modified` is emitted in full;
///   * two objects recurse and are included only if their sub-delta is
///     non-empty;
///   * scalars, arrays and cross-type changes are compared atomically — when
///     they differ the whole `modified` value is emitted (merge patch never
///     diffs array elements).
///
/// Matching this exactly is what keeps the exporter reconciler from clobbering
/// status fields written concurrently by the controller-service (`devices`,
/// `lastSeen`, `exporterStatus`, `statusMessage`): fields this pass did not
/// change are byte-identical to `original` and therefore never appear here.
fn merge_patch_diff(
    original: &serde_json::Value,
    modified: &serde_json::Value,
) -> serde_json::Value {
    use serde_json::{Map, Value};
    match (original, modified) {
        (Value::Object(a), Value::Object(b)) => {
            let mut into = Map::new();
            // Keys in `modified`: added, or changed relative to `original`.
            for (key, bv) in b {
                match a.get(key) {
                    // go: getDiff "value was added" -> into[key] = bv
                    None => {
                        into.insert(key.clone(), bv.clone());
                    }
                    Some(av) => {
                        if av.is_object() && bv.is_object() {
                            // go: recurse, include only when the sub-delta is non-empty
                            let sub = merge_patch_diff(av, bv);
                            if matches!(&sub, Value::Object(m) if !m.is_empty()) {
                                into.insert(key.clone(), sub);
                            }
                        } else if av != bv {
                            // go: type change / scalar / array -> into[key] = bv
                            into.insert(key.clone(), bv.clone());
                        }
                    }
                }
            }
            // go: getDiff "add all deleted values as nil"
            for key in a.keys() {
                if !b.contains_key(key) {
                    into.insert(key.clone(), Value::Null);
                }
            }
            Value::Object(into)
        }
        // Top-level non-objects (e.g. the status was absent, serializing to
        // `null`): an unchanged pair yields an empty patch, otherwise the whole
        // `modified` value replaces it.
        _ => {
            if original == modified {
                Value::Object(Map::new())
            } else {
                modified.clone()
            }
        }
    }
}

/// Build the status-subresource merge patch this reconciler sends: the delta
/// between the status as read (`original`) and after this pass (`modified`),
/// wrapped as `{ "status": <delta> }`. Port of `r.Status().Patch(ctx, &exporter,
/// original)` with `original = client.MergeFrom(exporter.DeepCopy())`.
fn status_merge_patch(
    original: &serde_json::Value,
    modified: &serde_json::Value,
) -> serde_json::Value {
    serde_json::json!({ "status": merge_patch_diff(original, modified) })
}

/// Reconcile a single `Exporter`. Port of `ExporterReconciler.Reconcile`.
pub async fn reconcile(
    exporter: Arc<Exporter>,
    ctx: Arc<Context>,
) -> Result<Action, ExporterError> {
    let namespace = exporter.namespace().unwrap_or_default();
    let name = exporter.name_any();
    let mut exporter = (*exporter).clone();

    // Snapshot the status exactly as read, before any mutation, so the patch
    // below emits only the delta this pass produces — the port of Go's
    // `original := client.MergeFrom(exporter.DeepCopy())` (exporter_controller.go:71).
    let original_status =
        serde_json::to_value(&exporter.status).unwrap_or_else(|_| serde_json::json!({}));

    let prev_online = online(&exporter);
    let prev_registered = registered(&exporter);

    // 1. credential secret <name>-exporter
    let secret_name = format!("{name}-exporter");
    let subject = exporter.internal_subject();
    let secret = ensure_secret(
        &ctx.client,
        &namespace,
        &secret_name,
        &ctx.signer,
        &subject,
        &exporter,
    )
    .await?;
    exporter
        .status
        .get_or_insert_with(ExporterStatus::default)
        .credential = Some(LocalObjectReference {
        name: secret.name_any(),
    });

    // 2. leaseRef from active leases
    let lease_ref = active_lease_ref(&ctx.client, &namespace, &name).await?;
    exporter
        .status
        .get_or_insert_with(ExporterStatus::default)
        .lease_ref = lease_ref;

    // 3. Online / Registered conditions + reported status
    let requeue = reconcile_status_conditions_online(&mut exporter, Timestamp::now());

    // 4. endpoint
    exporter
        .status
        .get_or_insert_with(ExporterStatus::default)
        .endpoint = Some(controller_endpoint());

    let new_online = online(&exporter);
    let new_registered = registered(&exporter);

    // 5. status patch (merge), then events only on success.
    //
    // Go patches with `client.MergeFrom(original)` (exporter_controller.go:71,96),
    // a `types.MergePatchType` whose body is `jsonpatch.CreateMergePatch` — an
    // RFC-7386 merge patch containing ONLY the fields this pass changed. This
    // reconciler must match that field-for-field: the controller-service status
    // stream concurrently writes `devices`, `lastSeen`, `exporterStatus` and
    // `statusMessage` via their own `MergeFrom` delta patches
    // (controller_service.go:291/324/360-363/645/664). Serializing the whole
    // status here would re-send those untouched keys with whatever (possibly
    // stale) values sat in the informer cache, regressing a concurrent
    // `ReportStatus`/`Status`-stream write. The delta omits every key this pass
    // did not touch; `leaseRef`'s transition from set to unset falls out as an
    // explicit `null` naturally (present in the read, absent after the reset).
    let api: Api<Exporter> = Api::namespaced(ctx.client.clone(), &namespace);
    let new_status =
        serde_json::to_value(&exporter.status).unwrap_or_else(|_| serde_json::json!({}));
    let patch = status_merge_patch(&original_status, &new_status);
    match api
        .patch_status(&name, &PatchParams::default(), &Patch::Merge(&patch))
        .await
    {
        Ok(_) => {}
        Err(err) if is_conflict(&err) => return Ok(Action::requeue(Duration::ZERO)),
        Err(err) => return Err(err.into()),
    }

    let events = exporter_events(
        &exporter,
        prev_online,
        prev_registered,
        new_online,
        new_registered,
        Timestamp::now(),
    );
    let object_ref = exporter.object_ref(&());
    for event in events {
        // Best-effort: a failed event publish must not fail the reconcile.
        if let Err(err) = ctx
            .recorder
            .publish(
                &Event {
                    type_: event.type_,
                    reason: event.reason.to_string(),
                    note: Some(event.note),
                    action: event.reason.to_string(),
                    secondary: None,
                },
                &object_ref,
            )
            .await
        {
            tracing::warn!(%name, error = %err, "failed to publish exporter event");
        }
    }

    // 6. requeue after 30s while online; otherwise rely on watches.
    Ok(if requeue.is_zero() {
        Action::await_change()
    } else {
        Action::requeue(requeue)
    })
}

/// Port of `reconcileStatusLeaseRef`: the active lease (label
/// `!jumpstarter.dev/lease-ended`) whose `status.exporterRef.name` is `name`,
/// if any.
async fn active_lease_ref(
    client: &Client,
    namespace: &str,
    name: &str,
) -> Result<Option<LocalObjectReference>, ExporterError> {
    let leases: Api<Lease> = Api::namespaced(client.clone(), namespace);
    let list = leases
        .list(&ListParams::default().labels(&format!("!{LEASE_LABEL_ENDED}")))
        .await?;

    let mut lease_ref = None;
    for lease in &list.items {
        let Some(status) = lease.status.as_ref() else {
            continue;
        };
        if !status.ended {
            if let Some(exporter_ref) = &status.exporter_ref {
                if exporter_ref.name == name {
                    lease_ref = Some(LocalObjectReference {
                        name: lease.name_any(),
                    });
                }
            }
        }
    }
    Ok(lease_ref)
}

/// Requeue policy on reconcile error: short backoff.
fn error_policy(_exporter: Arc<Exporter>, _err: &ExporterError, _ctx: Arc<Context>) -> Action {
    Action::requeue(Duration::from_secs(5))
}

/// Build and run the Exporter controller: `For(Exporter)`, `Owns(Lease)`,
/// `Owns(Secret)`. Runs until the stream ends.
pub async fn run(client: Client, signer: Arc<Signer>, namespace: String) {
    let reporter = Reporter {
        controller: "exporter-controller".into(),
        instance: std::env::var("CONTROLLER_POD_NAME").ok(),
    };
    let context = Arc::new(Context {
        client: client.clone(),
        signer,
        recorder: Recorder::new(client.clone(), reporter),
    });

    Controller::new(
        Api::<Exporter>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .owns(
        Api::<Lease>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .owns(
        Api::<Secret>::namespaced(client.clone(), &namespace),
        watcher::Config::default(),
    )
    .run(reconcile, error_policy, context)
    .for_each(|_| async {})
    .await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_api::device::Device;
    use jumpstarter_controller_api::exporter::ExporterSpec;
    use k8s_openapi::apimachinery::pkg::apis::meta::v1::{ObjectMeta, Time};

    const NOW: i64 = 1_700_000_000;

    fn ts(secs: i64) -> Timestamp {
        Timestamp::from_second(secs).unwrap()
    }

    fn exporter_with(status: ExporterStatus) -> Exporter {
        Exporter {
            metadata: ObjectMeta {
                name: Some("e".into()),
                namespace: Some("default".into()),
                generation: Some(7),
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: Some(status),
        }
    }

    fn online_cond(
        exporter: &Exporter,
    ) -> &k8s_openapi::apimachinery::pkg::apis::meta::v1::Condition {
        exporter
            .status
            .as_ref()
            .unwrap()
            .conditions
            .as_ref()
            .unwrap()
            .iter()
            .find(|c| c.type_ == EXPORTER_CONDITION_TYPE_ONLINE)
            .unwrap()
    }

    // go: exporter_controller.go:197-212 (never seen)
    #[test]
    fn never_seen_is_offline_without_reported_status() {
        let mut e = exporter_with(ExporterStatus::default());
        let requeue = reconcile_status_conditions_online(&mut e, ts(NOW));
        assert_eq!(requeue, Duration::ZERO);
        let c = online_cond(&e);
        assert_eq!(c.status, "False");
        assert_eq!(c.reason, "Seen");
        assert_eq!(c.message, "Never seen");
        // observedGeneration carries the object generation.
        assert_eq!(c.observed_generation, Some(7));
        // exporterStatus must NOT be forced to Offline for never-seen exporters.
        assert_eq!(e.status.as_ref().unwrap().exporter_status, None);
        assert_eq!(
            e.status.as_ref().unwrap().status_message.as_deref(),
            Some("Never seen")
        );
        assert!(!online(&e));
    }

    // go: exporter_controller.go:213-226 (last seen > 1 minute)
    #[test]
    fn stale_last_seen_marks_offline() {
        let status = ExporterStatus {
            last_seen: Some(Time(ts(NOW - 61))),
            ..Default::default()
        };
        let mut e = exporter_with(status);
        let requeue = reconcile_status_conditions_online(&mut e, ts(NOW));
        assert_eq!(requeue, Duration::ZERO);
        let c = online_cond(&e);
        assert_eq!(c.status, "False");
        assert_eq!(c.reason, "Seen");
        assert_eq!(c.message, "Last seen more than 1 minute ago");
        let s = e.status.as_ref().unwrap();
        assert_eq!(s.exporter_status, Some(ExporterStatusValue::Offline));
        assert_eq!(
            s.status_message.as_deref(),
            Some("Connection lost - last seen more than 1 minute ago")
        );
    }

    // Exactly one minute is NOT yet stale (Go uses strict `>`).
    #[test]
    fn exactly_one_minute_is_still_online() {
        let status = ExporterStatus {
            last_seen: Some(Time(ts(NOW - 60))),
            ..Default::default()
        };
        let mut e = exporter_with(status);
        let requeue = reconcile_status_conditions_online(&mut e, ts(NOW));
        assert_eq!(requeue, Duration::from_secs(30));
        assert_eq!(online_cond(&e).status, "True");
    }

    // go: exporter_controller.go:239-249 (recent, not reported offline -> online)
    #[test]
    fn recent_last_seen_is_online_and_requeues_30s() {
        let status = ExporterStatus {
            last_seen: Some(Time(ts(NOW - 5))),
            ..Default::default()
        };
        let mut e = exporter_with(status);
        let requeue = reconcile_status_conditions_online(&mut e, ts(NOW));
        assert_eq!(requeue, Duration::from_secs(30));
        let c = online_cond(&e);
        assert_eq!(c.status, "True");
        assert_eq!(c.message, "Last seen less than 1 minute ago");
        assert!(online(&e));
    }

    // go: exporter_controller.go:230-238 (recent but reported Offline -> graceful)
    #[test]
    fn recent_but_reported_offline_is_graceful_shutdown() {
        let status = ExporterStatus {
            last_seen: Some(Time(ts(NOW - 5))),
            exporter_status: Some(ExporterStatusValue::Offline),
            status_message: Some("bye".into()),
            ..Default::default()
        };
        let mut e = exporter_with(status);
        let requeue = reconcile_status_conditions_online(&mut e, ts(NOW));
        // No requeue for a reported-offline exporter.
        assert_eq!(requeue, Duration::ZERO);
        let c = online_cond(&e);
        assert_eq!(c.status, "False");
        assert_eq!(c.reason, "Offline");
        assert_eq!(c.message, "bye");
    }

    // go: exporter_controller.go:252-266 (Registered driven by devices)
    #[test]
    fn registered_condition_tracks_devices() {
        // No devices -> Unregister/False.
        let mut e = exporter_with(ExporterStatus::default());
        reconcile_status_conditions_online(&mut e, ts(NOW));
        assert!(!registered(&e));
        let reg = e
            .status
            .as_ref()
            .unwrap()
            .conditions
            .as_ref()
            .unwrap()
            .iter()
            .find(|c| c.type_ == EXPORTER_CONDITION_TYPE_REGISTERED)
            .unwrap();
        assert_eq!(reg.status, "False");
        assert_eq!(reg.reason, "Unregister");

        // With devices -> Register/True.
        let mut e = exporter_with(ExporterStatus {
            devices: Some(vec![Device::default()]),
            ..Default::default()
        });
        reconcile_status_conditions_online(&mut e, ts(NOW));
        assert!(registered(&e));
    }

    // go: exporter_controller.go:100-107 (registration event)
    #[test]
    fn events_registered_transition() {
        let e = exporter_with(ExporterStatus {
            devices: Some(vec![Device::default(), Device::default()]),
            ..Default::default()
        });
        let events = exporter_events(&e, false, false, false, true, ts(NOW));
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].reason, "ExporterRegistered");
        assert_eq!(
            events[0].note,
            "Exporter registered its capabilities: deviceCount=2"
        );
    }

    // go: exporter_controller.go:113-123 (offline event branches)
    #[test]
    fn events_offline_graceful_vs_connection_lost() {
        // Graceful: reported Offline + recent lastSeen.
        let e = exporter_with(ExporterStatus {
            last_seen: Some(Time(ts(NOW - 5))),
            exporter_status: Some(ExporterStatusValue::Offline),
            status_message: Some("draining".into()),
            ..Default::default()
        });
        let events = exporter_events(&e, true, true, false, true, ts(NOW));
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].reason, "ExporterOffline");
        assert!(events[0].note.contains("graceful shutdown"));
        assert!(events[0].note.contains("message=draining"));

        // Connection lost: stale lastSeen.
        let e = exporter_with(ExporterStatus {
            last_seen: Some(Time(ts(NOW - 120))),
            exporter_status: Some(ExporterStatusValue::Offline),
            ..Default::default()
        });
        let events = exporter_events(&e, true, true, false, true, ts(NOW));
        assert_eq!(events.len(), 1);
        assert!(events[0].note.contains("connection lost"));
    }

    #[test]
    fn controller_endpoint_default_and_override() {
        // NOTE: relies on process env; keep the two assertions ordered.
        std::env::remove_var("GRPC_ENDPOINT");
        assert_eq!(controller_endpoint(), "localhost:8082");
        std::env::set_var("GRPC_ENDPOINT", "grpc.example.com:8082");
        assert_eq!(controller_endpoint(), "grpc.example.com:8082");
        std::env::remove_var("GRPC_ENDPOINT");
    }

    // --- status merge-patch delta (Go: client.MergeFrom(exporter.DeepCopy())) ---

    // A reconcile that changes nothing must patch nothing (fixes the lastSeen /
    // conditions thrash against the status stream). go: CreateMergePatch of two
    // equal documents is `{}`.
    #[test]
    fn no_op_reconcile_produces_empty_status_delta() {
        let original = serde_json::json!({
            "devices": [{"uuid": "dev-1"}],
            "lastSeen": "2025-01-02T03:04:05Z",
            "exporterStatus": "Available",
            "conditions": [{"type": "Online", "status": "True", "reason": "Seen"}],
            "endpoint": "localhost:8082",
        });
        let delta = merge_patch_diff(&original, &original.clone());
        assert_eq!(delta, serde_json::json!({}));
        assert_eq!(
            status_merge_patch(&original, &original.clone()),
            serde_json::json!({ "status": {} })
        );
    }

    // The core clobber guard: fields this pass did not change never appear in the
    // patch, so a concurrent controller-service write of devices/lastSeen/
    // exporterStatus survives. go: getDiff omits keys whose value is unchanged.
    #[test]
    fn status_delta_carries_only_changed_fields() {
        let original = serde_json::json!({
            "devices": [{"uuid": "dev-1"}],
            "lastSeen": "2025-01-02T03:04:05Z",
            "exporterStatus": "Available",
            "conditions": [{"type": "Online", "status": "False", "reason": "Seen"}],
        });
        // Reconciler flipped Online True and set endpoint/credential; it left the
        // stream-owned devices/lastSeen/exporterStatus exactly as read.
        let modified = serde_json::json!({
            "devices": [{"uuid": "dev-1"}],
            "lastSeen": "2025-01-02T03:04:05Z",
            "exporterStatus": "Available",
            "conditions": [{"type": "Online", "status": "True", "reason": "Seen"}],
            "endpoint": "localhost:8082",
            "credential": {"name": "e-exporter"},
        });
        let delta = merge_patch_diff(&original, &modified);
        let obj = delta.as_object().unwrap();
        assert!(!obj.contains_key("devices"), "devices must not be re-sent");
        assert!(
            !obj.contains_key("lastSeen"),
            "lastSeen must not be re-sent"
        );
        assert!(
            !obj.contains_key("exporterStatus"),
            "exporterStatus must not be re-sent"
        );
        // conditions changed -> the WHOLE array is re-sent (merge patch is atomic
        // on arrays, matching CreateMergePatch).
        assert_eq!(delta["conditions"], modified["conditions"]);
        assert_eq!(delta["endpoint"], "localhost:8082");
        assert_eq!(
            delta["credential"],
            serde_json::json!({"name": "e-exporter"})
        );
        assert_eq!(obj.len(), 3);
    }

    // leaseRef present-in-read then reset to None serializes as an omitted key,
    // and the delta must emit it as an explicit `null` clear. go: getDiff "add all
    // deleted values as nil".
    #[test]
    fn status_delta_clears_lease_ref_as_null() {
        let original = serde_json::json!({
            "leaseRef": {"name": "lease-1"},
            "endpoint": "localhost:8082",
        });
        let modified = serde_json::json!({ "endpoint": "localhost:8082" });
        assert_eq!(
            merge_patch_diff(&original, &modified),
            serde_json::json!({ "leaseRef": null })
        );
    }

    // Nested objects recurse. go: getDiff `case map[string]interface{}` recurses.
    #[test]
    fn merge_patch_diff_recurses_into_objects() {
        let original = serde_json::json!({ "credential": {"name": "old"} });
        let modified = serde_json::json!({ "credential": {"name": "new"} });
        assert_eq!(
            merge_patch_diff(&original, &modified),
            serde_json::json!({ "credential": {"name": "new"} })
        );
    }

    // A newly-created exporter (status read as absent -> serde `null`) sends the
    // full status. go: getDiff({}, modified) returns every key.
    #[test]
    fn absent_read_status_sends_everything() {
        let original = serde_json::Value::Null;
        let modified = serde_json::json!({
            "endpoint": "localhost:8082",
            "credential": {"name": "e-exporter"},
        });
        assert_eq!(merge_patch_diff(&original, &modified), modified);
    }

    // End-to-end over the real `ExporterStatus` types, driven through the actual
    // condition logic: a reconcile that sets credential/endpoint/conditions and
    // clears leaseRef must not re-send the four stream-owned fields.
    #[test]
    fn reconcile_style_delta_preserves_stream_owned_status() {
        use jumpstarter_controller_api::device::Device;

        // Status as last written by the controller-service status stream.
        let read = ExporterStatus {
            devices: Some(vec![Device::default()]),
            last_seen: Some(Time(ts(NOW - 5))),
            exporter_status: Some(ExporterStatusValue::Available),
            status_message: Some("lease ready".into()),
            lease_ref: Some(LocalObjectReference {
                name: "lease-1".into(),
            }),
            ..Default::default()
        };

        // Reconcile a clone: set credential + endpoint, run the condition logic,
        // and (active lease ended) clear leaseRef.
        let mut e = exporter_with(read.clone());
        {
            let s = e.status.as_mut().unwrap();
            s.credential = Some(LocalObjectReference {
                name: "e-exporter".into(),
            });
            s.lease_ref = None;
        }
        reconcile_status_conditions_online(&mut e, ts(NOW));
        e.status.as_mut().unwrap().endpoint = Some("localhost:8082".into());

        let original = serde_json::to_value(Some(&read)).unwrap();
        let modified = serde_json::to_value(&e.status).unwrap();
        let delta = merge_patch_diff(&original, &modified);
        let obj = delta.as_object().unwrap();

        // Stream-owned fields the reconciler never touched: absent from the patch.
        assert!(!obj.contains_key("devices"), "devices must not be re-sent");
        assert!(
            !obj.contains_key("lastSeen"),
            "lastSeen must not be re-sent"
        );
        assert!(
            !obj.contains_key("exporterStatus"),
            "exporterStatus must not be re-sent"
        );
        assert!(
            !obj.contains_key("statusMessage"),
            "statusMessage must not be re-sent"
        );

        // Fields this pass owns: present.
        assert_eq!(
            delta["credential"],
            serde_json::json!({"name": "e-exporter"})
        );
        assert_eq!(delta["endpoint"], "localhost:8082");
        assert!(
            obj.contains_key("conditions"),
            "Online/Registered conditions were set this pass"
        );
        // leaseRef cleared -> explicit null.
        assert_eq!(delta["leaseRef"], serde_json::Value::Null);
    }
}
