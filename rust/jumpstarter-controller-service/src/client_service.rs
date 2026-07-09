//! The AIP-style resource API service, `jumpstarter.client.v1.ClientService`,
//! ported from `controller/internal/service/client/v1/client_service.go`
//! (behavioral reference) plus its helpers in
//! `controller/internal/service/utils/identifier.go`,
//! `controller/internal/service/auth/auth.go`, and the protobuf conversions in
//! `controller/api/v1alpha1/lease_helpers.go` / `exporter_helpers.go`.
//!
//! This is the client-facing surface: `GetExporter`/`ListExporters`,
//! `GetLease`/`ListLeases`/`CreateLease`/`UpdateLease`/`DeleteLease`, and
//! `RotateToken`. Every call is authenticated per-request via the [`AuthClient`]
//! port (the Go `Auth.AuthClient`, i.e. `VerifyClientObjectToken` +
//! namespace-match) and then performs plain Kubernetes CRUD against the
//! Exporter/Lease CRs and the client-credential `Secret`.
//!
//! ## Wire-visible contracts reproduced here
//!
//! - AIP resource-name parsing (`namespaces/{ns}/{kind}/{name}`) with Go's exact
//!   `INVALID_ARGUMENT` segment-count / segment-value error strings ([`identifier`]).
//! - `ListLeases` tag-filter keys are auto-prefixed with
//!   `metadata.jumpstarter.dev/`, and the `only_active` flag (nil-or-true =>
//!   active-only) adds a `!jumpstarter.dev/lease-ended` requirement.
//! - `CreateLease` mints a UUIDv7 name when `lease_id` is empty, and requires
//!   one of `selector` / `exporter_name` (`"one of selector or exporter_name is
//!   required"`).
//! - `DeleteLease` is a soft delete (`spec.release = true`) and is idempotent:
//!   a second delete of an already-released lease is
//!   `FAILED_PRECONDITION "lease %q has already been released"`.
//! - `RotateToken` re-signs the internal token and rewrites the `"token"` key of
//!   the `<client>-client` credential Secret.
//! - Lease-transfer / permission errors keep Go's plain-error wording (returned
//!   as gRPC `UNKNOWN`), and raw apiserver errors from Get/List/Create/Patch are
//!   forwarded verbatim as `UNKNOWN` (never remapped to `NOT_FOUND`).
//!
//! Raw apiserver errors from Get/List/Create/Patch are forwarded through the
//! shared [`crate::errors::forward_apiserver_error`], which reaches past kube's
//! wrapped `Display` to the apiserver `status.message` — the exact string Go's
//! `apierrors.StatusError.Error()` returns (just `.Message`). Other wire strings
//! in this module are still minted with inline `Status` constructors.

#![allow(clippy::result_large_err)] // tonic::Status is the RPC error convention.

use std::collections::BTreeMap;
use std::sync::Arc;

use chrono::{DateTime, Utc};
use k8s_openapi::api::core::v1::{LocalObjectReference, Secret};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, LabelSelectorRequirement, Time};
use k8s_openapi::jiff::Timestamp;
use kube::api::{Api, ListParams, Patch, PatchParams, PostParams};
use tonic::metadata::MetadataMap;
use tonic::{Request, Response, Status};

use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::exporter::{Exporter, ExporterStatusValue};
use jumpstarter_controller_api::go_duration::{GoDuration, SECOND};
use jumpstarter_controller_api::labels::{LEASE_LABEL_ENDED, LEASE_TAG_METADATA_PREFIX};
use jumpstarter_controller_api::lease::{validate_lease_tags, Lease, LeaseSpec};
use jumpstarter_controller_auth::signer::Signer;
use jumpstarter_controller_core::scheduler::selector::{
    format_label_selector, parse_label_selector, selector_is_empty,
};
use jumpstarter_controller_core::scheduler::time_fields::reconcile_lease_time_fields;

use jumpstarter_protocol::client_v1 as cpb;
use jumpstarter_protocol::client_v1::client_service_server::ClientService as ClientServiceTrait;

use crate::errors;

// ---------------------------------------------------------------------------
// Auth port (go: internal/service/auth/auth.go `Auth.AuthClient`)
// ---------------------------------------------------------------------------

/// The per-call client-authentication seam, factored into a trait so the
/// service is testable and does not hard-code the full authn/authz stack. It is
/// the Rust equivalent of Go's `auth.Auth.AuthClient(ctx, namespace)`
/// (`internal/service/auth/auth.go:37-55`): authenticate the bearer token,
/// authorize + resolve the `Client` CR (`oidc.VerifyClientObjectToken`), then
/// enforce that the request's `namespace` equals the client's namespace,
/// failing with `PERMISSION_DENIED "namespace mismatch"` otherwise.
///
/// The concrete implementation (validator union + [`jumpstarter_controller_auth`]
/// `authorize` + kube fetch) is wired by the server bootstrap and shared with
/// `ControllerService`; it is intentionally out of this module's scope.
#[tonic::async_trait]
pub trait AuthClient: Send + Sync + 'static {
    /// Authenticates the request and returns the authorized [`Client`] CR whose
    /// namespace matches `namespace`.
    async fn auth_client(&self, metadata: &MetadataMap, namespace: &str) -> Result<Client, Status>;
}

// ---------------------------------------------------------------------------
// Resource-name identifiers (go: internal/service/utils/identifier.go)
// ---------------------------------------------------------------------------

/// AIP resource-name parsing/formatting for `namespaces/{ns}/{kind}/{name}`.
///
/// This is the "identifier port": all resource-name handling flows through
/// these functions so the wire-visible `INVALID_ARGUMENT` strings stay in one
/// place, byte-identical to `identifier.go`.
pub mod identifier {
    use tonic::Status;

    /// A parsed namespaced object key (`kclient.ObjectKey`).
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct ObjectKey {
        /// The `namespaces/{namespace}/...` segment.
        pub namespace: String,
        /// The trailing `.../{name}` segment.
        pub name: String,
    }

    /// go: identifier.go:12-34 `ParseNamespaceIdentifier` — `namespaces/{ns}`.
    #[allow(clippy::result_large_err)]
    pub fn parse_namespace_identifier(identifier: &str) -> Result<String, Status> {
        let segments: Vec<&str> = identifier.split('/').collect();
        if segments.len() != 2 {
            return Err(Status::invalid_argument(format!(
                "invalid number of segments in identifier \"{identifier}\", expecting 2, got {}",
                segments.len()
            )));
        }
        if segments[0] != "namespaces" {
            return Err(Status::invalid_argument(format!(
                "invalid first segment in identifier \"{identifier}\", expecting \"namespaces\", got \"{}\"",
                segments[0]
            )));
        }
        Ok(segments[1].to_string())
    }

    /// go: identifier.go:36-71 `ParseObjectIdentifier` —
    /// `namespaces/{ns}/{kind}/{name}`.
    #[allow(clippy::result_large_err)]
    pub fn parse_object_identifier(identifier: &str, kind: &str) -> Result<ObjectKey, Status> {
        let segments: Vec<&str> = identifier.split('/').collect();
        if segments.len() != 4 {
            return Err(Status::invalid_argument(format!(
                "invalid number of segments in identifier \"{identifier}\", expecting 4, got {}",
                segments.len()
            )));
        }
        if segments[0] != "namespaces" {
            return Err(Status::invalid_argument(format!(
                "invalid first segment in identifier \"{identifier}\", expecting \"namespaces\", got \"{}\"",
                segments[0]
            )));
        }
        if segments[2] != kind {
            return Err(Status::invalid_argument(format!(
                "invalid third segment in identifier \"{identifier}\", expecting \"{kind}\", got \"{}\"",
                segments[2]
            )));
        }
        Ok(ObjectKey {
            namespace: segments[1].to_string(),
            name: segments[3].to_string(),
        })
    }

    /// go: identifier.go:73-75 `UnparseObjectIdentifier`.
    pub fn unparse_object_identifier(namespace: &str, kind: &str, name: &str) -> String {
        format!("namespaces/{namespace}/{kind}/{name}")
    }

    /// go: identifier.go:77-79 `ParseExporterIdentifier`.
    #[allow(clippy::result_large_err)]
    pub fn parse_exporter_identifier(identifier: &str) -> Result<ObjectKey, Status> {
        parse_object_identifier(identifier, "exporters")
    }

    /// go: identifier.go:81-83 `UnparseExporterIdentifier`.
    pub fn unparse_exporter_identifier(namespace: &str, name: &str) -> String {
        unparse_object_identifier(namespace, "exporters", name)
    }

    /// go: identifier.go:85-87 `ParseLeaseIdentifier`.
    #[allow(clippy::result_large_err)]
    pub fn parse_lease_identifier(identifier: &str) -> Result<ObjectKey, Status> {
        parse_object_identifier(identifier, "leases")
    }

    /// go: identifier.go:89-91 `UnparseLeaseIdentifier`.
    pub fn unparse_lease_identifier(namespace: &str, name: &str) -> String {
        unparse_object_identifier(namespace, "leases", name)
    }

    /// go: identifier.go:93-95 `ParseClientIdentifier`.
    #[allow(clippy::result_large_err)]
    pub fn parse_client_identifier(identifier: &str) -> Result<ObjectKey, Status> {
        parse_object_identifier(identifier, "clients")
    }
}

use identifier::ObjectKey;

// ---------------------------------------------------------------------------
// ListLeases selector construction (tag prefixing + only_active)
// ---------------------------------------------------------------------------

/// Go's `req.OnlyActive == nil || *req.OnlyActive`: active-only filtering is the
/// default, applied unless `only_active` is explicitly `false`.
///
/// go: client_service.go:173
pub fn only_active_default(only_active: Option<bool>) -> bool {
    only_active.is_none_or(|value| value)
}

/// Builds the label selector string for `ListLeases`, combining the user
/// `filter`, the tag filter (each requirement key auto-prefixed with
/// `metadata.jumpstarter.dev/`), and — when `only_active` — a
/// `!jumpstarter.dev/lease-ended` requirement. Returns `None` when the combined
/// selector is empty (match everything), so the caller omits the label filter
/// entirely rather than sending an invalid `"<none>"`.
///
/// go: client_service.go:143-190
#[allow(clippy::result_large_err)]
pub fn build_lease_selector(
    filter: &str,
    tag_filter: &str,
    only_active: bool,
) -> Result<Option<String>, Status> {
    // Base user filter. Go uses labels.Parse directly; we route through the
    // ParseLabelSelector port (its error text differs — a documented, wire-rare
    // divergence on the invalid-filter path).
    let mut selector =
        parse_label_selector(filter).map_err(|err| Status::unknown(err.to_string()))?;

    // Auto-prefix tag-filter keys with metadata.jumpstarter.dev/ and AND them in.
    if !tag_filter.is_empty() {
        let tag_selector = parse_label_selector(tag_filter)
            .map_err(|err| Status::invalid_argument(format!("invalid tag_filter: {err}")))?;

        if let Some(match_labels) = tag_selector.match_labels {
            let prefixed = selector.match_labels.get_or_insert_with(BTreeMap::new);
            for (key, value) in match_labels {
                prefixed.insert(format!("{LEASE_TAG_METADATA_PREFIX}{key}"), value);
            }
        }
        if let Some(exprs) = tag_selector.match_expressions {
            let target = selector.match_expressions.get_or_insert_with(Vec::new);
            for mut expr in exprs {
                expr.key = format!("{LEASE_TAG_METADATA_PREFIX}{}", expr.key);
                target.push(expr);
            }
        }
    }

    // Active-only: the lease-ended label must not exist.
    if only_active {
        selector
            .match_expressions
            .get_or_insert_with(Vec::new)
            .push(LabelSelectorRequirement {
                key: LEASE_LABEL_ENDED.to_string(),
                operator: "DoesNotExist".to_string(),
                values: Some(Vec::new()),
            });
    }

    if selector_is_empty(&selector) {
        Ok(None)
    } else {
        Ok(Some(format_label_selector(&selector)))
    }
}

/// Turns a user `filter` string into an optional label-selector string
/// (exporters have no tag/active machinery), `None` for an empty selector.
#[allow(clippy::result_large_err)]
fn filter_to_labels(filter: &str) -> Result<Option<String>, Status> {
    let selector = parse_label_selector(filter).map_err(|err| Status::unknown(err.to_string()))?;
    if selector_is_empty(&selector) {
        Ok(None)
    } else {
        Ok(Some(format_label_selector(&selector)))
    }
}

/// Builds [`ListParams`] from a label selector string, page size and page
/// token, mirroring Go's `ListOptions{LabelSelector, Limit, Continue}`.
fn list_params(labels: Option<String>, page_size: i32, page_token: &str) -> ListParams {
    let mut params = ListParams::default();
    if let Some(labels) = labels {
        params = params.labels(&labels);
    }
    if page_size > 0 {
        params = params.limit(page_size as u32);
    }
    if !page_token.is_empty() {
        params = params.continue_token(page_token);
    }
    params
}

// ---------------------------------------------------------------------------
// Soft-delete idempotence (go: client_service.go:381-383)
// ---------------------------------------------------------------------------

/// Rejects a delete of an already-released lease with
/// `FAILED_PRECONDITION "lease %q has already been released"`, making
/// `DeleteLease` idempotent. `name` is the request resource name (`%q`-quoted).
///
/// go: client_service.go:381-383
#[allow(clippy::result_large_err)]
fn check_not_released(lease: &Lease, name: &str) -> Result<(), Status> {
    if lease.spec.release {
        return Err(Status::failed_precondition(format!(
            "lease {name:?} has already been released"
        )));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Time conversions
// ---------------------------------------------------------------------------

fn proto_ts_to_chrono(ts: &prost_types::Timestamp) -> DateTime<Utc> {
    DateTime::from_timestamp(ts.seconds, ts.nanos.max(0) as u32).unwrap_or_default()
}

fn proto_duration_to_go(duration: &prost_types::Duration) -> GoDuration {
    GoDuration(duration.seconds * SECOND + i64::from(duration.nanos))
}

fn chrono_to_k8s_time(dt: DateTime<Utc>) -> Time {
    Time(
        Timestamp::new(dt.timestamp(), dt.timestamp_subsec_nanos() as i32)
            .expect("chrono instant is a valid jiff timestamp"),
    )
}

fn k8s_time_to_chrono(time: &Time) -> DateTime<Utc> {
    DateTime::from_timestamp(time.0.as_second(), time.0.subsec_nanosecond().max(0) as u32)
        .unwrap_or_default()
}

fn k8s_time_to_proto_ts(time: &Time) -> prost_types::Timestamp {
    prost_types::Timestamp {
        seconds: time.0.as_second(),
        nanos: time.0.subsec_nanosecond(),
    }
}

/// `durationpb.New` for a Go-nanosecond duration: seconds and nanos carry the
/// same sign, `|nanos| < 1e9` (integer division truncates toward zero like Go).
fn go_duration_to_proto(duration: GoDuration) -> prost_types::Duration {
    let nanos = duration.0;
    prost_types::Duration {
        seconds: nanos / SECOND,
        nanos: (nanos % SECOND) as i32,
    }
}

fn timestamp_total_nanos(time: &Time) -> i128 {
    i128::from(time.0.as_second()) * 1_000_000_000 + i128::from(time.0.subsec_nanosecond())
}

// ---------------------------------------------------------------------------
// Protobuf conversions (go: lease_helpers.go / exporter_helpers.go)
// ---------------------------------------------------------------------------

/// Port of `LeaseFromProtobuf` (go: lease_helpers.go:200-268): builds a `Lease`
/// CR from the request message, its namespaced key, and the owning client ref.
/// Parses the selector, reconciles the time triple, and splits user tags into
/// prefixed ObjectMeta labels + unprefixed `spec.tags`.
#[allow(clippy::result_large_err)]
pub fn lease_from_protobuf(
    req: &cpb::Lease,
    key: &ObjectKey,
    client_ref_name: &str,
) -> Result<Lease, Status> {
    let selector =
        parse_label_selector(&req.selector).map_err(|err| Status::unknown(err.to_string()))?;

    let mut begin_time = req.begin_time.as_ref().map(proto_ts_to_chrono);
    let mut end_time = req.end_time.as_ref().map(proto_ts_to_chrono);
    let mut duration = req.duration.as_ref().map(proto_duration_to_go);
    reconcile_lease_time_fields(&mut begin_time, &mut end_time, &mut duration)
        .map_err(|err| Status::unknown(err.to_string()))?;

    // ObjectMeta labels: selector matchLabels (excluding the reserved prefix),
    // then prefixed user tags.
    let mut meta_labels: BTreeMap<String, String> = BTreeMap::new();
    if let Some(match_labels) = &selector.match_labels {
        for (key, value) in match_labels {
            if key.starts_with(LEASE_TAG_METADATA_PREFIX) {
                continue;
            }
            meta_labels.insert(key.clone(), value.clone());
        }
    }
    for (key, value) in &req.tags {
        meta_labels.insert(format!("{LEASE_TAG_METADATA_PREFIX}{key}"), value.clone());
    }

    // spec.tags: user tags without prefix (None when empty, like Go).
    let spec_tags = if req.tags.is_empty() {
        None
    } else {
        Some(
            req.tags
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
        )
    };

    let exporter_ref = req
        .exporter_name
        .as_ref()
        .filter(|name| !name.is_empty())
        .map(|name| LocalObjectReference { name: name.clone() });

    let mut lease = Lease::new(
        &key.name,
        LeaseSpec {
            client_ref: LocalObjectReference {
                name: client_ref_name.to_string(),
            },
            duration,
            selector,
            exporter_ref,
            tags: spec_tags,
            release: false,
            begin_time: begin_time.map(chrono_to_k8s_time),
            end_time: end_time.map(chrono_to_k8s_time),
        },
    );
    lease.metadata.namespace = Some(key.namespace.clone());
    if !meta_labels.is_empty() {
        lease.metadata.labels = Some(meta_labels);
    }
    Ok(lease)
}

/// Port of `(*Lease).ToProtobuf` (go: lease_helpers.go:270-328).
pub fn lease_to_protobuf(lease: &Lease) -> cpb::Lease {
    let namespace = lease.metadata.namespace.as_deref().unwrap_or_default();
    let name = lease.metadata.name.as_deref().unwrap_or_default();

    let conditions = lease
        .status
        .as_ref()
        .map(|status| status.conditions.as_slice())
        .unwrap_or_default()
        .iter()
        .map(condition_to_protobuf)
        .collect();

    let mut out = cpb::Lease {
        name: identifier::unparse_lease_identifier(namespace, name),
        selector: format_label_selector(&lease.spec.selector),
        client: Some(identifier::unparse_object_identifier(
            namespace,
            "clients",
            &lease.spec.client_ref.name,
        )),
        conditions,
        tags: lease
            .spec
            .tags
            .as_ref()
            .map(|tags| tags.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default(),
        ..Default::default()
    };

    if let Some(exporter_ref) = &lease.spec.exporter_ref {
        out.exporter_name = Some(exporter_ref.name.clone());
    }
    if let Some(duration) = lease.spec.duration {
        out.duration = Some(go_duration_to_proto(duration));
    }
    // Requested/planned times from spec.
    if let Some(begin) = &lease.spec.begin_time {
        out.begin_time = Some(k8s_time_to_proto_ts(begin));
    }
    if let Some(end) = &lease.spec.end_time {
        out.end_time = Some(k8s_time_to_proto_ts(end));
    }

    // Actual times from status.
    if let Some(status) = &lease.status {
        if let Some(status_begin) = &status.begin_time {
            out.effective_begin_time = Some(k8s_time_to_proto_ts(status_begin));
            // End = status.EndTime or now; effective duration is non-negative.
            let end_nanos = match &status.end_time {
                Some(status_end) => {
                    out.effective_end_time = Some(k8s_time_to_proto_ts(status_end));
                    timestamp_total_nanos(status_end)
                }
                None => {
                    let now = Timestamp::now();
                    i128::from(now.as_second()) * 1_000_000_000
                        + i128::from(now.subsec_nanosecond())
                }
            };
            let effective = (end_nanos - timestamp_total_nanos(status_begin)).max(0);
            out.effective_duration = Some(go_duration_to_proto(GoDuration(effective as i64)));
        }
        if let Some(exporter_ref) = &status.exporter_ref {
            out.exporter = Some(identifier::unparse_exporter_identifier(
                namespace,
                &exporter_ref.name,
            ));
        }
    }

    out
}

/// The RFC-7386 (JSON Merge Patch) delta that turns `original` into `modified`.
///
/// This is the body controller-runtime's `client.MergeFrom(original)` produces
/// and `client.Patch` sends: a minimal diff of only the changed keys, with no
/// `resourceVersion` (a plain `MergeFrom`, unlike `MergeFromWithOptimisticLock`,
/// omits it). `UpdateLease` uses it to write only the lease-spec fields it
/// mutated without pinning the read-time `resourceVersion` or re-asserting the
/// rest of the (possibly concurrently-changed) spec.
///
/// Behaviour, matching `jsonpatch.CreateMergePatch` / `getDiff`
/// (`github.com/evanphx/json-patch`): a key present in `original` but absent in
/// `modified` becomes an explicit `null` (a *clear*); a key added in `modified`
/// is emitted in full; two objects recurse and are included only when their
/// sub-delta is non-empty; scalars/arrays/cross-type changes are compared
/// atomically and the whole `modified` value is emitted when they differ.
///
/// Kept in lockstep with `jumpstarter_controller_core`'s private
/// `merge_patch_diff` (the exporter reconciler's status delta); consolidating
/// the two into one shared helper is a follow-up.
fn merge_diff(original: &serde_json::Value, modified: &serde_json::Value) -> serde_json::Value {
    use serde_json::{Map, Value};
    match (original, modified) {
        (Value::Object(a), Value::Object(b)) => {
            let mut into = Map::new();
            // Keys in `modified`: added, or changed relative to `original`.
            for (key, bv) in b {
                match a.get(key) {
                    None => {
                        into.insert(key.clone(), bv.clone());
                    }
                    Some(av) => {
                        if av.is_object() && bv.is_object() {
                            let sub = merge_diff(av, bv);
                            if matches!(&sub, Value::Object(m) if !m.is_empty()) {
                                into.insert(key.clone(), sub);
                            }
                        } else if av != bv {
                            into.insert(key.clone(), bv.clone());
                        }
                    }
                }
            }
            // Keys dropped in `modified` are cleared with an explicit `null`.
            for key in a.keys() {
                if !b.contains_key(key) {
                    into.insert(key.clone(), Value::Null);
                }
            }
            Value::Object(into)
        }
        _ => {
            if original == modified {
                Value::Object(Map::new())
            } else {
                modified.clone()
            }
        }
    }
}

/// Maps a `metav1.Condition` to the wire `Condition` (go: lease_helpers.go:272-283).
/// Every field is always populated on the wire (Go takes the address of each
/// value, so even empty/zero fields serialize as present).
fn condition_to_protobuf(condition: &Condition) -> jumpstarter_protocol::v1::Condition {
    jumpstarter_protocol::v1::Condition {
        r#type: Some(condition.type_.clone()),
        status: Some(condition.status.clone()),
        observed_generation: Some(condition.observed_generation.unwrap_or(0)),
        last_transition_time: Some(jumpstarter_protocol::v1::Time {
            seconds: Some(condition.last_transition_time.0.as_second()),
            nanos: Some(condition.last_transition_time.0.subsec_nanosecond()),
        }),
        reason: Some(condition.reason.clone()),
        message: Some(condition.message.clone()),
    }
}

/// Port of `(*Exporter).ToProtobuf` (go: exporter_helpers.go:28-39).
pub fn exporter_to_protobuf(exporter: &Exporter) -> cpb::Exporter {
    let namespace = exporter.metadata.namespace.as_deref().unwrap_or_default();
    let name = exporter.metadata.name.as_deref().unwrap_or_default();

    let status = exporter.status.as_ref();
    let online = status
        .and_then(|status| status.conditions.as_ref())
        .is_some_and(|conditions| is_status_condition_true(conditions, "Online"));
    let exporter_status = status.and_then(|status| status.exporter_status);
    let status_message = status
        .and_then(|status| status.status_message.clone())
        .unwrap_or_default();

    #[allow(deprecated)]
    cpb::Exporter {
        name: identifier::unparse_exporter_identifier(namespace, name),
        labels: exporter
            .metadata
            .labels
            .as_ref()
            .map(|labels| labels.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default(),
        online,
        status: exporter_status_to_proto(exporter_status) as i32,
        status_message,
    }
}

/// `meta.IsStatusConditionTrue` for the `Online` condition (go: exporter_helpers.go:30).
fn is_status_condition_true(conditions: &[Condition], type_: &str) -> bool {
    conditions
        .iter()
        .any(|c| c.type_ == type_ && c.status == "True")
}

/// `stringToProtoStatus` (go: exporter_helpers.go:42-61): CRD status enum to the
/// wire `ExporterStatus` (unknown/`Unspecified` => `UNSPECIFIED`).
fn exporter_status_to_proto(
    status: Option<ExporterStatusValue>,
) -> jumpstarter_protocol::v1::ExporterStatus {
    use jumpstarter_protocol::v1::ExporterStatus as Pb;
    match status.unwrap_or(ExporterStatusValue::Unspecified) {
        ExporterStatusValue::Unspecified => Pb::Unspecified,
        ExporterStatusValue::Offline => Pb::Offline,
        ExporterStatusValue::Available => Pb::Available,
        ExporterStatusValue::BeforeLeaseHook => Pb::BeforeLeaseHook,
        ExporterStatusValue::LeaseReady => Pb::LeaseReady,
        ExporterStatusValue::AfterLeaseHook => Pb::AfterLeaseHook,
        ExporterStatusValue::BeforeLeaseHookFailed => Pb::BeforeLeaseHookFailed,
        ExporterStatusValue::AfterLeaseHookFailed => Pb::AfterLeaseHookFailed,
    }
}

// ---------------------------------------------------------------------------
// The service
// ---------------------------------------------------------------------------

/// The `jumpstarter.client.v1.ClientService` implementation, generic over the
/// [`AuthClient`] port. Mirrors Go's `ClientService` struct (`kclient.Client`
/// + `auth.Auth` + `MaxTags` + `oidc.Signer`).
///
/// go: client_service.go:42-57
pub struct ClientService<A: AuthClient> {
    client: kube::Client,
    auth: A,
    signer: Arc<Signer>,
    max_tags: i32,
}

impl<A: AuthClient> ClientService<A> {
    /// go: client_service.go:50-57 `NewClientService`.
    pub fn new(client: kube::Client, auth: A, max_tags: i32, signer: Arc<Signer>) -> Self {
        Self {
            client,
            auth,
            signer,
            max_tags,
        }
    }

    fn leases(&self, namespace: &str) -> Api<Lease> {
        Api::namespaced(self.client.clone(), namespace)
    }

    fn exporters(&self, namespace: &str) -> Api<Exporter> {
        Api::namespaced(self.client.clone(), namespace)
    }

    fn secrets(&self, namespace: &str) -> Api<Secret> {
        Api::namespaced(self.client.clone(), namespace)
    }
}

#[tonic::async_trait]
impl<A: AuthClient> ClientServiceTrait for ClientService<A> {
    /// go: client_service.go:59-79 `GetExporter`.
    async fn get_exporter(
        &self,
        request: Request<cpb::GetExporterRequest>,
    ) -> Result<Response<cpb::Exporter>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let key = identifier::parse_exporter_identifier(&req.name)?;
        self.auth.auth_client(&metadata, &key.namespace).await?;

        let exporter = self
            .exporters(&key.namespace)
            .get(&key.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        Ok(Response::new(exporter_to_protobuf(&exporter)))
    }

    /// go: client_service.go:81-111 `ListExporters`.
    async fn list_exporters(
        &self,
        request: Request<cpb::ListExportersRequest>,
    ) -> Result<Response<cpb::ListExportersResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let namespace = identifier::parse_namespace_identifier(&req.parent)?;
        self.auth.auth_client(&metadata, &namespace).await?;

        let labels = filter_to_labels(&req.filter)?;
        let params = list_params(labels, req.page_size, &req.page_token);
        let list = self
            .exporters(&namespace)
            .list(&params)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        Ok(Response::new(cpb::ListExportersResponse {
            exporters: list.items.iter().map(exporter_to_protobuf).collect(),
            next_page_token: list.metadata.continue_.unwrap_or_default(),
        }))
    }

    /// go: client_service.go:113-130 `GetLease`.
    async fn get_lease(
        &self,
        request: Request<cpb::GetLeaseRequest>,
    ) -> Result<Response<cpb::Lease>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let key = identifier::parse_lease_identifier(&req.name)?;
        self.auth.auth_client(&metadata, &key.namespace).await?;

        let lease = self
            .leases(&key.namespace)
            .get(&key.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        Ok(Response::new(lease_to_protobuf(&lease)))
    }

    /// go: client_service.go:132-206 `ListLeases`.
    async fn list_leases(
        &self,
        request: Request<cpb::ListLeasesRequest>,
    ) -> Result<Response<cpb::ListLeasesResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let namespace = identifier::parse_namespace_identifier(&req.parent)?;
        self.auth.auth_client(&metadata, &namespace).await?;

        let labels = build_lease_selector(
            &req.filter,
            &req.tag_filter,
            only_active_default(req.only_active),
        )?;
        let params = list_params(labels, req.page_size, &req.page_token);
        let list = self
            .leases(&namespace)
            .list(&params)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        Ok(Response::new(cpb::ListLeasesResponse {
            leases: list.items.iter().map(lease_to_protobuf).collect(),
            next_page_token: list.metadata.continue_.unwrap_or_default(),
        }))
    }

    /// go: client_service.go:208-256 `CreateLease`.
    async fn create_lease(
        &self,
        request: Request<cpb::CreateLeaseRequest>,
    ) -> Result<Response<cpb::Lease>, Status> {
        let (metadata, _ext, req) = request.into_parts();

        let lease = req
            .lease
            .as_ref()
            .ok_or_else(|| Status::invalid_argument("lease is required"))?;
        validate_lease_target(lease)?;

        let tags: BTreeMap<String, String> = lease
            .tags
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        validate_lease_tags(&tags, self.max_tags as usize)
            .map_err(|err| Status::invalid_argument(format!("invalid lease tags: {err}")))?;

        let namespace = identifier::parse_namespace_identifier(&req.parent)?;
        let jclient = self.auth.auth_client(&metadata, &namespace).await?;

        // Use the provided lease_id, else a fresh UUIDv7.
        let name = if req.lease_id.is_empty() {
            uuid::Uuid::now_v7().to_string()
        } else {
            req.lease_id.clone()
        };

        let key = ObjectKey {
            namespace: namespace.clone(),
            name,
        };
        let jlease = lease_from_protobuf(
            lease,
            &key,
            jclient.metadata.name.as_deref().unwrap_or_default(),
        )?;

        let created = self
            .leases(&namespace)
            .create(&PostParams::default(), &jlease)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        Ok(Response::new(lease_to_protobuf(&created)))
    }

    /// go: client_service.go:272-359 `UpdateLease`.
    async fn update_lease(
        &self,
        request: Request<cpb::UpdateLeaseRequest>,
    ) -> Result<Response<cpb::Lease>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let lease_req = req
            .lease
            .as_ref()
            .ok_or_else(|| Status::invalid_argument("lease is required"))?;

        let key = identifier::parse_lease_identifier(&lease_req.name)?;
        let jclient = self.auth.auth_client(&metadata, &key.namespace).await?;

        let leases = self.leases(&key.namespace);
        let mut jlease = leases
            .get(&key.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        let client_name = jclient.metadata.name.as_deref().unwrap_or_default();
        if jlease.spec.client_ref.name != client_name {
            return Err(Status::unknown("UpdateLease permission denied"));
        }

        // go: client_service.go:292 `original := kclient.MergeFrom(jlease.DeepCopy())`.
        // Snapshot the object as read so the write below can send only the
        // fields this handler mutates (see the patch site at the end).
        let original = jlease.clone();

        let has_ref = jlease
            .status
            .as_ref()
            .is_some_and(|status| status.exporter_ref.is_some());
        let ended = jlease.status.as_ref().is_some_and(|status| status.ended);

        let updating_times = lease_req.begin_time.is_some()
            || lease_req.duration.is_some()
            || lease_req.end_time.is_some();

        if updating_times {
            // Desired times parsed from the request (client_ref unchanged here).
            let desired = lease_from_protobuf(lease_req, &key, &jlease.spec.client_ref.name)?;

            // BeginTime is only updatable before the lease starts.
            if lease_req.begin_time.is_some() {
                if has_ref {
                    let same = match (&jlease.spec.begin_time, &desired.spec.begin_time) {
                        (Some(current), Some(desired)) => {
                            k8s_time_to_chrono(current) == k8s_time_to_chrono(desired)
                        }
                        _ => false,
                    };
                    if !same {
                        return Err(Status::unknown(
                            "cannot update BeginTime: lease has already started",
                        ));
                    }
                }
                jlease.spec.begin_time = desired.spec.begin_time.clone();
            }
            if lease_req.duration.is_some() {
                jlease.spec.duration = desired.spec.duration;
            }
            if lease_req.end_time.is_some() {
                jlease.spec.end_time = desired.spec.end_time.clone();
            }
        }

        // Transfer the lease to a new client (the `client` field).
        if let Some(new_client) = lease_req.client.as_ref().filter(|value| !value.is_empty()) {
            if !has_ref {
                return Err(Status::unknown(
                    "cannot transfer lease: lease has not started yet",
                ));
            }
            if ended {
                return Err(Status::unknown(
                    "cannot transfer lease: lease has already ended",
                ));
            }
            let new_client_key = identifier::parse_client_identifier(new_client)?;
            if new_client_key.namespace != key.namespace {
                return Err(Status::unknown(
                    "cannot transfer lease to client in different namespace",
                ));
            }
            let clients: Api<Client> = Api::namespaced(self.client.clone(), &key.namespace);
            clients
                .get(&new_client_key.name)
                .await
                .map_err(|err| Status::unknown(format!("target client not found: {err}")))?;
            jlease.spec.client_ref.name = new_client_key.name;
        }

        // Recalculate / validate the time triple only when times were touched.
        if updating_times {
            let mut begin = jlease.spec.begin_time.as_ref().map(k8s_time_to_chrono);
            let mut end = jlease.spec.end_time.as_ref().map(k8s_time_to_chrono);
            let mut duration = jlease.spec.duration;
            reconcile_lease_time_fields(&mut begin, &mut end, &mut duration)
                .map_err(|err| Status::unknown(err.to_string()))?;
            jlease.spec.begin_time = begin.map(chrono_to_k8s_time);
            jlease.spec.end_time = end.map(chrono_to_k8s_time);
            jlease.spec.duration = duration;
        }

        // go: client_service.go:354 `s.Patch(ctx, &jlease, original)` where
        // `original := kclient.MergeFrom(jlease.DeepCopy())` (line 292). A plain
        // `MergeFrom` (no `MergeFromWithOptimisticLock`) sends the RFC-7386 delta
        // of only the fields this handler changed — deliberately WITHOUT
        // `metadata.resourceVersion`. That matters twice: (1) a merge-patch body
        // never carries resourceVersion, so the frequent concurrent
        // `lease.status` writes from the lease reconciler do not turn this into a
        // 409 Conflict; (2) spec fields we did not touch (e.g. `spec.release`,
        // set by a racing release) are omitted, so they are not reverted to the
        // Get-time value. Serializing the whole `jlease` with `Patch::Merge`
        // would re-assert the stale spec and pin resourceVersion.
        let original_json = serde_json::to_value(&original)
            .map_err(|err| Status::unknown(format!("failed to serialize lease: {err}")))?;
        let modified_json = serde_json::to_value(&jlease)
            .map_err(|err| Status::unknown(format!("failed to serialize lease: {err}")))?;
        let patch = merge_diff(&original_json, &modified_json);

        let patched = leases
            .patch(&key.name, &PatchParams::default(), &Patch::Merge(&patch))
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        Ok(Response::new(lease_to_protobuf(&patched)))
    }

    /// go: client_service.go:361-394 `DeleteLease`.
    async fn delete_lease(
        &self,
        request: Request<cpb::DeleteLeaseRequest>,
    ) -> Result<Response<()>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let key = identifier::parse_lease_identifier(&req.name)?;
        let jclient = self.auth.auth_client(&metadata, &key.namespace).await?;

        let leases = self.leases(&key.namespace);
        let jlease = leases
            .get(&key.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        let client_name = jclient.metadata.name.as_deref().unwrap_or_default();
        if jlease.spec.client_ref.name != client_name {
            return Err(Status::unknown("DeleteLease permission denied"));
        }

        // Idempotent soft delete: a re-release is FAILED_PRECONDITION.
        check_not_released(&jlease, &req.name)?;

        leases
            .patch(
                &key.name,
                &PatchParams::default(),
                &Patch::Merge(serde_json::json!({ "spec": { "release": true } })),
            )
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        Ok(Response::new(()))
    }

    /// go: client_service.go:396-449 `RotateToken`.
    async fn rotate_token(
        &self,
        request: Request<cpb::RotateTokenRequest>,
    ) -> Result<Response<cpb::RotateTokenResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let namespace = identifier::parse_namespace_identifier(&req.parent)?;
        let jclient = self.auth.auth_client(&metadata, &namespace).await?;

        let token = self
            .signer
            .token(&jclient.internal_subject())
            .map_err(|err| Status::internal(format!("failed to sign token: {err}")))?;

        let client_name = jclient.metadata.name.as_deref().unwrap_or_default();
        let secret_name = format!("{client_name}-client");
        // Fetch (to fail with the Go message shape if absent), then patch the
        // "token" key. Secret data is base64 in the k8s JSON wire form.
        self.secrets(&namespace)
            .get(&secret_name)
            .await
            .map_err(|err| Status::internal(format!("failed to get credential secret: {err}")))?;

        let token_b64 =
            base64::Engine::encode(&base64::engine::general_purpose::STANDARD, token.as_bytes());
        self.secrets(&namespace)
            .patch(
                &secret_name,
                &PatchParams::default(),
                &Patch::Merge(serde_json::json!({ "data": { "token": token_b64 } })),
            )
            .await
            .map_err(|err| {
                Status::internal(format!("failed to update credential secret: {err}"))
            })?;

        tracing::info!(client = %client_name, %namespace, "token rotated");

        // Surface the token's expiry (parsed without verification, like Go).
        let expiry = unverified_exp(&token).map(|exp| prost_types::Timestamp {
            seconds: exp,
            nanos: 0,
        });

        Ok(Response::new(cpb::RotateTokenResponse { token, expiry }))
    }
}

/// go: client_service.go:258-270 `validateLeaseTarget` — one of selector or
/// exporter_name must be set.
#[allow(clippy::result_large_err)]
fn validate_lease_target(lease: &cpb::Lease) -> Result<(), Status> {
    let has_selector = !lease.selector.is_empty();
    let has_exporter_name = lease
        .exporter_name
        .as_ref()
        .is_some_and(|name| !name.is_empty());
    if !has_selector && !has_exporter_name {
        return Err(Status::invalid_argument(
            "one of selector or exporter_name is required",
        ));
    }
    Ok(())
}

/// Reads the `exp` claim from a JWT without verifying it, mirroring Go's
/// `jwt.ParseUnverified` in `RotateToken` (go: client_service.go:432-443).
fn unverified_exp(token: &str) -> Option<i64> {
    let payload = token.split('.').nth(1)?;
    let bytes =
        base64::Engine::decode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, payload).ok()?;
    let value: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    value.get("exp")?.as_i64()
}

#[cfg(test)]
mod tests {
    use super::*;

    // -- identifier parsing (go: identifier.go / table) ---------------------

    #[test]
    fn parse_namespace_identifier_ok() {
        let ns = identifier::parse_namespace_identifier("namespaces/default").unwrap();
        assert_eq!(ns, "default");
    }

    #[test]
    fn parse_namespace_identifier_segment_count() {
        let err = identifier::parse_namespace_identifier("namespaces/a/b").unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert_eq!(
            err.message(),
            "invalid number of segments in identifier \"namespaces/a/b\", expecting 2, got 3"
        );
    }

    #[test]
    fn parse_namespace_identifier_first_segment() {
        let err = identifier::parse_namespace_identifier("ns/default").unwrap_err();
        assert_eq!(
            err.message(),
            "invalid first segment in identifier \"ns/default\", expecting \"namespaces\", got \"ns\""
        );
    }

    #[test]
    fn parse_object_identifier_ok() {
        let key = identifier::parse_exporter_identifier("namespaces/default/exporters/e1").unwrap();
        assert_eq!(key.namespace, "default");
        assert_eq!(key.name, "e1");

        let key = identifier::parse_lease_identifier("namespaces/ns/leases/l1").unwrap();
        assert_eq!(key.namespace, "ns");
        assert_eq!(key.name, "l1");

        let key = identifier::parse_client_identifier("namespaces/ns/clients/c1").unwrap();
        assert_eq!(key.name, "c1");
    }

    #[test]
    fn parse_object_identifier_segment_count() {
        let err = identifier::parse_lease_identifier("namespaces/ns/leases").unwrap_err();
        assert_eq!(
            err.message(),
            "invalid number of segments in identifier \"namespaces/ns/leases\", expecting 4, got 3"
        );
    }

    #[test]
    fn parse_object_identifier_wrong_kind() {
        let err = identifier::parse_lease_identifier("namespaces/ns/exporters/x").unwrap_err();
        assert_eq!(
            err.message(),
            "invalid third segment in identifier \"namespaces/ns/exporters/x\", expecting \"leases\", got \"exporters\""
        );
    }

    #[test]
    fn parse_object_identifier_wrong_first_segment() {
        let err = identifier::parse_exporter_identifier("ns/x/exporters/y").unwrap_err();
        assert_eq!(
            err.message(),
            "invalid first segment in identifier \"ns/x/exporters/y\", expecting \"namespaces\", got \"ns\""
        );
    }

    #[test]
    fn unparse_round_trips() {
        assert_eq!(
            identifier::unparse_exporter_identifier("default", "e1"),
            "namespaces/default/exporters/e1"
        );
        assert_eq!(
            identifier::unparse_lease_identifier("ns", "l1"),
            "namespaces/ns/leases/l1"
        );
    }

    // -- only_active nil-or-true (go: client_service.go:173) ----------------

    #[test]
    fn only_active_defaults_to_true() {
        assert!(only_active_default(None));
        assert!(only_active_default(Some(true)));
        assert!(!only_active_default(Some(false)));
    }

    // -- tag prefixing + active-only selector (go: client_service.go:143-190)

    #[test]
    fn build_selector_prefixes_tag_keys_and_adds_active() {
        let selector = build_lease_selector("", "team=devops", true)
            .unwrap()
            .expect("non-empty selector");
        assert!(
            selector.contains("metadata.jumpstarter.dev/team=devops"),
            "{selector}"
        );
        assert!(
            selector.contains("!jumpstarter.dev/lease-ended"),
            "{selector}"
        );
    }

    #[test]
    fn build_selector_active_only_toggles_ended_requirement() {
        let with_active = build_lease_selector("", "", true).unwrap();
        assert_eq!(with_active.as_deref(), Some("!jumpstarter.dev/lease-ended"));
        // only_active = false, no filter, no tags => match everything (None).
        let without = build_lease_selector("", "", false).unwrap();
        assert_eq!(without, None);
    }

    #[test]
    fn build_selector_combines_filter_and_tags() {
        let selector = build_lease_selector("board=rpi4", "team=devops", false)
            .unwrap()
            .expect("selector");
        assert!(selector.contains("board=rpi4"), "{selector}");
        assert!(
            selector.contains("metadata.jumpstarter.dev/team=devops"),
            "{selector}"
        );
        assert!(!selector.contains("lease-ended"), "{selector}");
    }

    #[test]
    fn build_selector_prefixes_tag_expression_keys() {
        // A `!=` tag filter becomes a NotIn expression whose key is prefixed.
        let selector = build_lease_selector("", "env!=prod", false)
            .unwrap()
            .expect("selector");
        assert!(
            selector.contains("metadata.jumpstarter.dev/env notin (prod)"),
            "{selector}"
        );
    }

    #[test]
    fn build_selector_invalid_tag_filter_is_invalid_argument() {
        let err = build_lease_selector("", "===", true).unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert!(
            err.message().starts_with("invalid tag_filter:"),
            "{}",
            err.message()
        );
    }

    // -- UUIDv7 name generation (go: client_service.go:232-239) -------------

    #[test]
    fn generated_lease_name_is_uuid_v7() {
        for _ in 0..16 {
            let name = uuid::Uuid::now_v7().to_string();
            let parsed = uuid::Uuid::parse_str(&name).expect("valid uuid string");
            assert_eq!(parsed.get_version_num(), 7, "expected UUIDv7: {name}");
            assert_eq!(name.len(), 36, "canonical hyphenated form");
        }
    }

    // -- soft-delete idempotence (go: client_service.go:381-383) ------------

    #[test]
    fn check_not_released_is_idempotent_failed_precondition() {
        let mut lease = Lease::new(
            "l1",
            LeaseSpec {
                client_ref: LocalObjectReference { name: "c1".into() },
                ..Default::default()
            },
        );

        // Not yet released: ok.
        check_not_released(&lease, "namespaces/ns/leases/l1").unwrap();

        // Already released: FAILED_PRECONDITION with the %q-quoted name.
        lease.spec.release = true;
        let err = check_not_released(&lease, "namespaces/ns/leases/l1").unwrap_err();
        assert_eq!(err.code(), tonic::Code::FailedPrecondition);
        assert_eq!(
            err.message(),
            "lease \"namespaces/ns/leases/l1\" has already been released"
        );
    }

    // -- validate_lease_target (go: client_service.go:258-270) --------------

    #[test]
    fn validate_lease_target_requires_selector_or_exporter_name() {
        let empty = cpb::Lease::default();
        let err = validate_lease_target(&empty).unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert_eq!(
            err.message(),
            "one of selector or exporter_name is required"
        );

        let with_selector = cpb::Lease {
            selector: "board=rpi4".into(),
            ..Default::default()
        };
        validate_lease_target(&with_selector).unwrap();

        let with_exporter = cpb::Lease {
            exporter_name: Some("device-1".into()),
            ..Default::default()
        };
        validate_lease_target(&with_exporter).unwrap();

        // Present-but-empty exporter_name does not satisfy the requirement.
        let empty_exporter = cpb::Lease {
            exporter_name: Some(String::new()),
            ..Default::default()
        };
        assert!(validate_lease_target(&empty_exporter).is_err());
    }

    // -- lease_from_protobuf tag/label split (go: lease_helpers.go:226-245) --

    #[test]
    fn lease_from_protobuf_splits_tags_into_prefixed_labels() {
        let mut tags = std::collections::HashMap::new();
        tags.insert("team".to_string(), "devops".to_string());
        let req = cpb::Lease {
            selector: "board=rpi4".into(),
            exporter_name: Some("device-1".into()),
            duration: Some(prost_types::Duration {
                seconds: 3600,
                nanos: 0,
            }),
            tags,
            ..Default::default()
        };
        let key = ObjectKey {
            namespace: "default".into(),
            name: "l1".into(),
        };
        let lease = lease_from_protobuf(&req, &key, "client-a").unwrap();

        assert_eq!(lease.metadata.name.as_deref(), Some("l1"));
        assert_eq!(lease.metadata.namespace.as_deref(), Some("default"));
        assert_eq!(lease.spec.client_ref.name, "client-a");
        assert_eq!(
            lease.spec.exporter_ref.as_ref().map(|r| r.name.as_str()),
            Some("device-1")
        );
        assert_eq!(lease.spec.duration, Some(GoDuration(3600 * SECOND)));

        // spec.tags are unprefixed; ObjectMeta labels carry the selector label
        // plus the prefixed user tag.
        let spec_tags = lease.spec.tags.as_ref().unwrap();
        assert_eq!(spec_tags.get("team").map(String::as_str), Some("devops"));
        let labels = lease.metadata.labels.as_ref().unwrap();
        assert_eq!(labels.get("board").map(String::as_str), Some("rpi4"));
        assert_eq!(
            labels
                .get("metadata.jumpstarter.dev/team")
                .map(String::as_str),
            Some("devops")
        );
    }

    #[test]
    fn lease_from_protobuf_no_tags_leaves_spec_tags_none() {
        let req = cpb::Lease {
            selector: "board=rpi4".into(),
            duration: Some(prost_types::Duration {
                seconds: 60,
                nanos: 0,
            }),
            ..Default::default()
        };
        let key = ObjectKey {
            namespace: "ns".into(),
            name: "l".into(),
        };
        let lease = lease_from_protobuf(&req, &key, "c").unwrap();
        assert_eq!(lease.spec.tags, None);
        assert!(lease.spec.exporter_ref.is_none());
    }

    // -- lease_to_protobuf name/format (go: lease_helpers.go:286-297) -------

    #[test]
    fn lease_to_protobuf_formats_names_and_selector() {
        let mut lease = Lease::new(
            "l1",
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: "client-a".into(),
                },
                selector: k8s_openapi::apimachinery::pkg::apis::meta::v1::LabelSelector {
                    match_labels: Some(
                        [("board".to_string(), "rpi4".to_string())]
                            .into_iter()
                            .collect(),
                    ),
                    match_expressions: None,
                },
                duration: Some(GoDuration(90 * SECOND)),
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some("default".into());

        let out = lease_to_protobuf(&lease);
        assert_eq!(out.name, "namespaces/default/leases/l1");
        assert_eq!(
            out.client.as_deref(),
            Some("namespaces/default/clients/client-a")
        );
        assert_eq!(out.selector, "board=rpi4");
        assert_eq!(
            out.duration,
            Some(prost_types::Duration {
                seconds: 90,
                nanos: 0,
            })
        );
    }

    // -- apiserver-error forwarding contract --------------------------------
    // go: client_service.go GetLease/ListLeases/CreateLease/UpdateLease/
    // DeleteLease all `return nil, err`, letting grpc-go surface the raw
    // apiserver `StatusError` as UNKNOWN with `.Error()` == `.Message`.

    /// Every ClientService Get/List/Create/Patch failure is now routed through
    /// [`errors::forward_apiserver_error`] (as the handler call sites do), so a
    /// raw apiserver error surfaces as gRPC `UNKNOWN` carrying the apiserver
    /// `status.message` verbatim — exactly what Go's `apierrors.StatusError`
    /// `.Error()` (just `.Message`) yields on `return nil, err`. It must NOT be
    /// kube's wrapped `Display` (`"ApiError: {msg}: {reason} (…)"`), which is
    /// what the removed local `kube_status(err.to_string())` produced and which
    /// no deployed client would match, and must NOT be remapped to `NOT_FOUND`.
    #[test]
    fn apiserver_errors_forward_status_message_not_kube_display() {
        let mut api =
            kube::core::Status::failure("leases.jumpstarter.dev \"absent\" not found", "NotFound");
        api.code = 404;
        let err = kube::Error::Api(Box::new(api));

        let status = errors::forward_apiserver_error(&err);
        assert_eq!(status.code(), tonic::Code::Unknown);
        assert_ne!(status.code(), tonic::Code::NotFound);
        assert_eq!(
            status.message(),
            "leases.jumpstarter.dev \"absent\" not found"
        );

        // Guards the regression: kube's Display diverges from Go's text, so the
        // forwarded message must not be the old `err.to_string()` output.
        assert_ne!(status.message(), err.to_string());
        assert!(
            !status.message().contains("ApiError"),
            "leaked kube Display: {}",
            status.message()
        );
    }

    // -- UpdateLease merge-patch is a minimal diff --------------------------
    // go: client_service.go:292/354 — `original := kclient.MergeFrom(jlease.
    // DeepCopy())` + `s.Patch(ctx, &jlease, original)`. The patch body must be
    // the delta of only the mutated spec fields: no `resourceVersion` (else a
    // concurrent `lease.status` write from the lease reconciler makes the
    // apiserver reject this with a 409 Conflict), and no untouched spec fields
    // (else a concurrently-set `spec.release` is reverted to the Get-time value).

    /// The lease exactly as read by `UpdateLease`'s `Get`: it carries a
    /// `resourceVersion` and a `spec.release` that a racing release just set.
    fn fetched_lease() -> Lease {
        let mut lease = Lease::new(
            "l1",
            LeaseSpec {
                client_ref: LocalObjectReference { name: "c1".into() },
                duration: Some(GoDuration(SECOND)),
                release: true,
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some("ns".into());
        lease.metadata.resource_version = Some("12345".into());
        lease
    }

    /// A duration-only update produces `{"spec":{"duration":"2s"}}` — nothing
    /// else. Proves both divergences the fix closes.
    #[test]
    fn update_lease_patch_omits_resource_version_and_untouched_spec() {
        let original = fetched_lease();
        // What `UpdateLease` mutates for a duration change (client_service.rs
        // `jlease.spec.duration = desired.spec.duration`).
        let mut modified = original.clone();
        modified.spec.duration = Some(GoDuration(2 * SECOND));

        let patch = merge_diff(
            &serde_json::to_value(&original).unwrap(),
            &serde_json::to_value(&modified).unwrap(),
        );

        // (1) No resourceVersion / metadata precondition in the merge body.
        assert!(
            patch.get("metadata").is_none(),
            "patch must not carry metadata (resourceVersion): {patch}"
        );
        assert!(patch.pointer("/metadata/resourceVersion").is_none());
        // (2) The concurrently-set spec.release is not re-asserted / reverted.
        assert!(
            patch.pointer("/spec/release").is_none(),
            "patch must not revert concurrent spec fields: {patch}"
        );
        // Only the field the handler changed is present.
        assert_eq!(
            patch.pointer("/spec/duration").and_then(|v| v.as_str()),
            Some("2s")
        );
        let spec = patch
            .pointer("/spec")
            .and_then(|v| v.as_object())
            .expect("spec object in patch");
        assert_eq!(
            spec.len(),
            1,
            "spec delta must hold only the changed field: {patch}"
        );
    }

    /// A client transfer produces only the nested `clientRef.name` delta, and a
    /// no-op update produces an empty patch — matching `MergeFrom`.
    #[test]
    fn update_lease_patch_transfer_and_noop() {
        let original = fetched_lease();

        let mut transferred = original.clone();
        transferred.spec.client_ref.name = "c2".into();
        let patch = merge_diff(
            &serde_json::to_value(&original).unwrap(),
            &serde_json::to_value(&transferred).unwrap(),
        );
        assert_eq!(
            patch,
            serde_json::json!({ "spec": { "clientRef": { "name": "c2" } } }),
            "transfer patch must be the minimal clientRef delta"
        );

        // Nothing mutated => empty merge patch (a no-op), never the whole object.
        let noop = merge_diff(
            &serde_json::to_value(&original).unwrap(),
            &serde_json::to_value(&original).unwrap(),
        );
        assert_eq!(noop, serde_json::json!({}));
    }
}
