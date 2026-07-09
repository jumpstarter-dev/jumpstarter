//! The `Lease` CRD: a client's request for (and grant of) an exporter.
//!
//! Leases represent a request for a specific exporter by a client. The lease
//! is acquired by the client and the exporter is assigned to the lease. The
//! lease is released by the client when the client is done with the exporter.
//! For more information see the Jumpstarter documentation:
//!
//! - <https://jumpstarter.dev/main/reference/man-pages/jmp.html#jmp-create-lease>
//! - <https://jumpstarter.dev/main/reference/man-pages/jmp.html#jmp-shell>
//!
//! go: controller/api/v1alpha1/lease_types.go + lease_helpers.go
//!
//! Deferred ports (kept next to the code that will need them):
//!
//! - TODO(phase-4 scheduler core): `ReconcileLeaseTimeFields`
//!   (go: lease_helpers.go:57-78) — calculates/validates the
//!   BeginTime/EndTime/Duration triple (6 supported patterns); its error
//!   strings cross the wire, so it is ported with `scheduler/time_fields.rs`.
//! - TODO(phase-4 scheduler core): `ParseLabelSelector`
//!   (go: lease_helpers.go:83-175) and `metav1.FormatLabelSelector` — the
//!   string-selector round trip used by the gRPC surface; ported with
//!   `scheduler/selector.rs`.
//! - TODO(phase-5 services): `LeaseFromProtobuf` / `Lease.ToProtobuf` /
//!   `LeaseList.ToProtobuf` (go: lease_helpers.go:200-339) — protobuf
//!   conversions belong to `jumpstarter-controller-service`.

use std::collections::BTreeMap;
use std::fmt;

use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, LabelSelector, Time};
use k8s_openapi::jiff::Timestamp;
use kube::{CustomResource, KubeSchema};
use serde::{Deserialize, Serialize};

use crate::go_duration::GoDuration;
// The Go originals declare these alongside the Lease types
// (go: lease_types.go:80-86); the crate keeps all wire-visible label/name
// constants together in `crate::labels`.
use crate::labels::LEASE_TAG_METADATA_PREFIX;

/// LeaseSpec defines the desired state of Lease
///
/// go: lease_types.go:27-51 `LeaseSpec` (CEL markers at lease_types.go:24-25)
#[derive(CustomResource, KubeSchema, Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[kube(
    group = "jumpstarter.dev",
    version = "v1alpha1",
    kind = "Lease",
    namespaced,
    status = "LeaseStatus",
    doc = "Lease is the Schema for the leases API",
    derive = "Default",
    derive = "PartialEq",
    printcolumn = r#"{"name":"Ended", "type":"boolean", "jsonPath":".status.ended"}"#,
    printcolumn = r#"{"name":"Client", "type":"string", "jsonPath":".spec.clientRef.name"}"#,
    printcolumn = r#"{"name":"Exporter", "type":"string", "jsonPath":".status.exporterRef.name"}"#,
    // Tolerate a spec-less object (Go `json:"spec,omitempty"`) by defaulting
    // the spec; the schemars transform strips the resulting `spec.default` so
    // `::crd()` is unchanged. See `crate::schema::strip_spec_default`. A live
    // Lease is never spec-less (the CEL rule below requires selector or
    // exporterRef), but the reconciler's `Api::<Lease>::list()` must still not
    // fail-closed on one, so the tolerance is applied uniformly.
    attr = "cfg_attr(all(), serde(default))",
    attr = "cfg_attr(all(), schemars(transform = crate::schema::strip_spec_default))"
)]
// The two spec-level CEL rules, rule + message copied VERBATIM from the
// kubebuilder markers (go: lease_types.go:24-25); they are wire-visible
// apiserver behavior and must match the golden CRD byte-for-byte.
#[x_kube(
    validation = Rule::new("((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0)) || (has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0)").message("one of selector or exporterRef.name is required"),
    validation = Rule::new("!has(oldSelf.tags) || self.tags == oldSelf.tags").message("tags are immutable after creation")
)]
// controller-gen keeps the non-pointer, kubebuilder-defaulted `selector` in
// `required` (the apiserver defaults it before validating); schemars treats a
// serde-defaulted field as optional under its deserialize contract, so the
// container transform re-adds it to the schema's `required` list.
// go: lease_types.go:34-36 (Selector, +kubebuilder:default:={})
#[schemars(transform = require_selector)]
#[serde(rename_all = "camelCase")]
pub struct LeaseSpec {
    /// The client that is requesting the lease
    #[schemars(transform = crate::schema::local_object_reference)]
    pub client_ref: LocalObjectReference,
    /// Duration of the lease. Must be positive when provided.
    /// Can be omitted (nil) when both BeginTime and EndTime are provided,
    /// in which case it's calculated as EndTime - BeginTime.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration: Option<GoDuration>,
    /// The selector for the exporter to be used
    // Go marker `+kubebuilder:default:={}` → `default: {}` (pinned in the
    // transform). controller-gen additionally keeps `selector` in `required`
    // (non-pointer Go field); schemars drops serde-defaulted fields from
    // `required`, so `#[schemars(required)]` re-adds it.
    #[serde(default)]
    #[schemars(required, transform = crate::schema::label_selector_defaulted)]
    pub selector: LabelSelector,
    /// Optionally pin this lease to a specific exporter name.
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::local_object_reference)]
    pub exporter_ref: Option<LocalObjectReference>,
    /// User-defined tags for the lease. Immutable after creation.
    /// Maximum 10 entries. Keys must be simple names (no slashes) conforming to Kubernetes label rules.
    // Go marker `+kubebuilder:validation:MaxProperties=10`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(extend("maxProperties" = 10))]
    pub tags: Option<BTreeMap<String, String>>,
    /// The release flag requests the controller to end the lease now
    // Go `json:"release,omitempty"` on a non-pointer bool: false serializes as
    // absent, absent deserializes as false.
    #[serde(default, skip_serializing_if = "is_false")]
    pub release: bool,
    /// Requested start time. If omitted, lease starts when exporter is acquired.
    /// Immutable after lease starts (cannot change the past).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub begin_time: Option<Time>,
    /// Requested end time. If specified with BeginTime, Duration is calculated.
    /// Can be updated to extend or shorten active leases.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end_time: Option<Time>,
}

/// LeaseStatus defines the observed state of Lease.
///
/// go: lease_types.go:54-69 `LeaseStatus`
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, schemars::JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct LeaseStatus {
    /// BeginTime is the actual start time of the lease.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub begin_time: Option<Time>,
    /// EndTime is the actual end time of the lease.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end_time: Option<Time>,
    /// ExporterRef is a reference to the exporter assigned to this lease.
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::local_object_reference)]
    pub exporter_ref: Option<LocalObjectReference>,
    /// Ended indicates whether the lease has been terminated.
    pub ended: bool,
    /// Priority is the effective priority of the lease from the access policy.
    // Go `Priority int json:"priority,omitempty"`: 0 serializes as absent.
    // controller-gen emits a bare `type: integer` (no format) for Go `int`;
    // `extend("format" = null)` cancels schemars' `format: int64` (the null is
    // dropped when the schema round-trips through typed JSONSchemaProps).
    #[serde(default, skip_serializing_if = "is_zero")]
    #[schemars(extend("format" = null))]
    pub priority: i64,
    /// SpotAccess indicates whether this lease was granted with spot (preemptible) access.
    #[serde(default, skip_serializing_if = "is_false")]
    pub spot_access: bool,
    /// Conditions represent the latest available observations of the lease state.
    // Go `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    #[schemars(transform = crate::schema::conditions)]
    pub conditions: Vec<Condition>,
}

/// Container-level schema transform for [`LeaseSpec`]: adds `selector` to the
/// schema's `required` list (see the comment on the `#[schemars(transform)]`
/// attribute for why schemars drops it).
fn require_selector(schema: &mut schemars::Schema) {
    let object = schema.ensure_object();
    let required = object
        .entry("required")
        .or_insert_with(|| serde_json::Value::Array(vec![]))
        .as_array_mut()
        .expect("required is an array");
    if !required.iter().any(|v| v == "selector") {
        required.push("selector".into());
        required.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
    }
}

/// Serde helper mirroring Go's `omitempty` on a non-pointer bool.
fn is_false(value: &bool) -> bool {
    !*value
}

/// Serde helper mirroring Go's `omitempty` on a non-pointer int.
fn is_zero(value: &i64) -> bool {
    *value == 0
}

/// Condition types recorded in `LeaseStatus.conditions`.
///
/// go: lease_types.go:71-78 `LeaseConditionType`
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum LeaseConditionType {
    /// The lease is waiting for an exporter to become available.
    Pending,
    /// The lease has an exporter and is active.
    Ready,
    /// The lease can never be satisfied (no exporter matches).
    Unsatisfiable,
    /// The lease spec is invalid.
    Invalid,
}

impl LeaseConditionType {
    /// The Go string constant for this condition type (the canonical strings
    /// live in [`crate::conditions`]).
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pending => crate::conditions::LEASE_CONDITION_TYPE_PENDING,
            Self::Ready => crate::conditions::LEASE_CONDITION_TYPE_READY,
            Self::Unsatisfiable => crate::conditions::LEASE_CONDITION_TYPE_UNSATISFIABLE,
            Self::Invalid => crate::conditions::LEASE_CONDITION_TYPE_INVALID,
        }
    }
}

impl fmt::Display for LeaseConditionType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl Lease {
    /// Sets the `Pending` condition to true.
    ///
    /// go: lease_helpers.go:345-347 `(*Lease).SetStatusPending`
    pub fn set_status_pending(&mut self, reason: &str, message: impl Into<String>) {
        self.set_status_condition(LeaseConditionType::Pending, true, reason, message);
    }

    /// Sets the `Ready` condition to the given status.
    ///
    /// go: lease_helpers.go:349-351 `(*Lease).SetStatusReady`
    pub fn set_status_ready(&mut self, status: bool, reason: &str, message: impl Into<String>) {
        self.set_status_condition(LeaseConditionType::Ready, status, reason, message);
    }

    /// Sets the `Unsatisfiable` condition to true.
    ///
    /// go: lease_helpers.go:353-355 `(*Lease).SetStatusUnsatisfiable`
    pub fn set_status_unsatisfiable(&mut self, reason: &str, message: impl Into<String>) {
        self.set_status_condition(LeaseConditionType::Unsatisfiable, true, reason, message);
    }

    /// Sets the `Invalid` condition to true.
    ///
    /// go: lease_helpers.go:357-359 `(*Lease).SetStatusInvalid`
    pub fn set_status_invalid(&mut self, reason: &str, message: impl Into<String>) {
        self.set_status_condition(LeaseConditionType::Invalid, true, reason, message);
    }

    /// Upserts a status condition with `observedGeneration` taken from
    /// `metadata.generation` and `lastTransitionTime` of now, using
    /// `meta.SetStatusCondition` semantics (`lastTransitionTime` only moves
    /// when the condition *status* changes).
    ///
    /// go: lease_helpers.go:361-384 `(*Lease).SetStatusCondition`
    pub fn set_status_condition(
        &mut self,
        condition: LeaseConditionType,
        status: bool,
        reason: &str,
        message: impl Into<String>,
    ) {
        let status_condition = if status { "True" } else { "False" };
        let generation = self.metadata.generation.unwrap_or(0);
        let conditions = &mut self.status.get_or_insert_with(Default::default).conditions;
        set_status_condition(
            conditions,
            Condition {
                type_: condition.as_str().to_owned(),
                status: status_condition.to_owned(),
                // Go's Condition.ObservedGeneration is `int64` with
                // `omitempty`: 0 marshals as absent, hence the None mapping.
                observed_generation: (generation != 0).then_some(generation),
                last_transition_time: Time(Timestamp::now()),
                reason: reason.to_owned(),
                message: message.into(),
            },
        );
    }

    /// The name of the assigned exporter, or `"(none)"` when unassigned
    /// (used in log/error messages).
    ///
    /// go: lease_helpers.go:386-391 `(*Lease).GetExporterName`
    pub fn get_exporter_name(&self) -> &str {
        match self.status.as_ref().and_then(|s| s.exporter_ref.as_ref()) {
            None => "(none)",
            Some(reference) => &reference.name,
        }
    }

    /// The name of the requesting client.
    ///
    /// go: lease_helpers.go:393-395 `(*Lease).GetClientName`
    pub fn get_client_name(&self) -> &str {
        &self.spec.client_ref.name
    }

    /// Marks the lease as released: `Ready=False/Released`, `ended=true`,
    /// `status.endTime=now`.
    ///
    /// The Go original also logs
    /// `"The lease has been marked for release" lease=... exporter=... client=...`;
    /// this crate is logging-free (pure data), so the phase-4 reconciler emits
    /// that log line at the call site.
    ///
    /// go: lease_helpers.go:397-403 `(*Lease).Release`
    pub fn release(&mut self) {
        self.set_status_ready(false, "Released", "The lease was marked for release");
        let status = self.status.get_or_insert_with(Default::default);
        status.ended = true;
        status.end_time = Some(Time(Timestamp::now()));
    }

    /// Marks the lease as expired: `Ready=False/Expired`, `ended=true`,
    /// `status.endTime=now`.
    ///
    /// The Go original also logs `"The lease has expired" ...` (see
    /// [`Lease::release`] for why logging happens at the call site).
    ///
    /// go: lease_helpers.go:405-411 `(*Lease).Expire`
    pub fn expire(&mut self) {
        self.set_status_ready(false, "Expired", "The lease has expired");
        let status = self.status.get_or_insert_with(Default::default);
        status.ended = true;
        status.end_time = Some(Time(Timestamp::now()));
    }

    /// Converts `spec.selector` into an evaluable selector, like
    /// `metav1.LabelSelectorAsSelector`.
    ///
    /// go: lease_helpers.go:341-343 `(*Lease).GetExporterSelector`
    pub fn get_exporter_selector(
        &self,
    ) -> Result<kube::core::Selector, kube::core::ParseExpressionError> {
        kube::core::Selector::try_from(self.spec.selector.clone())
    }
}

/// Port of `k8s.io/apimachinery/pkg/api/meta.SetStatusCondition` (the parts
/// exercised by the Lease helpers, which always stamp a non-zero
/// `lastTransitionTime`): upserts by `type`, moving `lastTransitionTime` only
/// when `status` changes, and returns whether anything changed.
///
/// TODO(phase-1 dedupe): fold into `crate::conditions` if the shared
/// `meta.SetStatusCondition` port lands there.
fn set_status_condition(conditions: &mut Vec<Condition>, new_condition: Condition) -> bool {
    let Some(existing) = conditions
        .iter_mut()
        .find(|c| c.type_ == new_condition.type_)
    else {
        conditions.push(new_condition);
        return true;
    };

    let mut changed = false;
    if existing.status != new_condition.status {
        existing.status = new_condition.status;
        existing.last_transition_time = new_condition.last_transition_time;
        changed = true;
    }
    if existing.reason != new_condition.reason {
        existing.reason = new_condition.reason;
        changed = true;
    }
    if existing.message != new_condition.message {
        existing.message = new_condition.message;
        changed = true;
    }
    if existing.observed_generation != new_condition.observed_generation {
        existing.observed_generation = new_condition.observed_generation;
        changed = true;
    }
    changed
}

/// Errors from [`validate_lease_tags`], with messages byte-identical to the
/// Go originals (verified by executing `lease_helpers.go` +
/// `k8s.io/apimachinery/pkg/util/validation` v0.33.0, the version pinned by
/// controller/go.mod).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum LeaseTagsError {
    /// go: lease_helpers.go:180
    #[error("too many tags: {count} (maximum {max})")]
    TooManyTags {
        /// Number of tags supplied.
        count: usize,
        /// Configured maximum.
        max: usize,
    },
    /// go: lease_helpers.go:184
    #[error("tag key {key:?} must not use reserved prefix")]
    ReservedPrefix {
        /// The offending tag key.
        key: String,
    },
    /// go: lease_helpers.go:187
    #[error("tag key {key:?} must not contain '/' (use simple names without prefix)")]
    KeyContainsSlash {
        /// The offending tag key.
        key: String,
    },
    /// go: lease_helpers.go:191
    #[error("tag key {key:?} is not a valid label key: {errors}")]
    InvalidLabelKey {
        /// The offending tag key.
        key: String,
        /// The `"; "`-joined validation messages.
        errors: String,
    },
    /// go: lease_helpers.go:194
    #[error("tag value for key {key:?} is not a valid label value: {errors}")]
    InvalidLabelValue {
        /// The tag key whose value is invalid.
        key: String,
        /// The `"; "`-joined validation messages.
        errors: String,
    },
}

/// ValidateLeaseTags validates user-defined lease tags against the given
/// maxTags limit.
///
/// Unlike the Go original (which iterates a Go map in random order and stops
/// at the first invalid entry), iteration here is deterministic (BTreeMap key
/// order); with a single invalid entry — the only case the error string
/// contract covers — the outputs are identical.
///
/// go: lease_helpers.go:177-198 `ValidateLeaseTags`
pub fn validate_lease_tags(
    tags: &BTreeMap<String, String>,
    max_tags: usize,
) -> Result<(), LeaseTagsError> {
    if tags.len() > max_tags {
        return Err(LeaseTagsError::TooManyTags {
            count: tags.len(),
            max: max_tags,
        });
    }
    for (k, v) in tags {
        if k.starts_with(LEASE_TAG_METADATA_PREFIX) || k.starts_with("jumpstarter.dev/") {
            return Err(LeaseTagsError::ReservedPrefix { key: k.clone() });
        }
        if k.contains('/') {
            return Err(LeaseTagsError::KeyContainsSlash { key: k.clone() });
        }
        let prefixed_key = format!("{LEASE_TAG_METADATA_PREFIX}{k}");
        let errs = is_qualified_name(&prefixed_key);
        if !errs.is_empty() {
            return Err(LeaseTagsError::InvalidLabelKey {
                key: k.clone(),
                errors: errs.join("; "),
            });
        }
        let errs = is_valid_label_value(v);
        if !errs.is_empty() {
            return Err(LeaseTagsError::InvalidLabelValue {
                key: k.clone(),
                errors: errs.join("; "),
            });
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Ports of k8s.io/apimachinery/pkg/util/validation (validation.go), private to
// this module because their message strings are embedded verbatim in
// LeaseTagsError. The regex patterns appear inside the messages, so both the
// matcher logic and the pattern *strings* are reproduced.
// ---------------------------------------------------------------------------

const QUALIFIED_NAME_FMT: &str = "([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]";
const QUALIFIED_NAME_ERR_MSG: &str = "must consist of alphanumeric characters, '-', '_' or '.', and must start and end with an alphanumeric character";
const QUALIFIED_NAME_MAX_LENGTH: usize = 63;

const LABEL_VALUE_FMT: &str = "(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?";
const LABEL_VALUE_ERR_MSG: &str = "a valid label must be an empty string or consist of alphanumeric characters, '-', '_' or '.', and must start and end with an alphanumeric character";
const LABEL_VALUE_MAX_LENGTH: usize = 63;

const DNS1123_SUBDOMAIN_FMT: &str =
    "[a-z0-9]([-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*";
const DNS1123_SUBDOMAIN_ERR_MSG: &str = "a lowercase RFC 1123 subdomain must consist of lower case alphanumeric characters, '-' or '.', and must start and end with an alphanumeric character";
const DNS1123_SUBDOMAIN_MAX_LENGTH: usize = 253;

const EMPTY_ERROR: &str = "must be non-empty";

/// go: validation.go `MaxLenError`
fn max_len_error(length: usize) -> String {
    format!("must be no more than {length} characters")
}

/// go: validation.go `RegexError` — note the Go quirk that each example ends
/// with `"', "` *before* the `" or "` separator, producing double spaces.
fn regex_error(msg: &str, fmt: &str, examples: &[&str]) -> String {
    if examples.is_empty() {
        return format!("{msg} (regex used for validation is '{fmt}')");
    }
    let mut out = String::from(msg);
    out.push_str(" (e.g. ");
    for (i, example) in examples.iter().enumerate() {
        if i > 0 {
            out.push_str(" or ");
        }
        out.push('\'');
        out.push_str(example);
        out.push_str("', ");
    }
    out.push_str("regex used for validation is '");
    out.push_str(fmt);
    out.push_str("')");
    out
}

/// Matcher equivalent of the anchored `qualifiedNameFmt` regex
/// `^([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]$` (byte-wise, like Go regexp on
/// UTF-8: any non-ASCII byte fails the character classes).
fn matches_qualified_name(value: &str) -> bool {
    fn ext(c: u8) -> bool {
        c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.')
    }
    match value.as_bytes() {
        [] => false,
        [c] => c.is_ascii_alphanumeric(),
        [first, mid @ .., last] => {
            first.is_ascii_alphanumeric()
                && last.is_ascii_alphanumeric()
                && mid.iter().copied().all(ext)
        }
    }
}

/// Matcher equivalent of the anchored `dns1123SubdomainFmt` regex.
fn matches_dns1123_subdomain(value: &str) -> bool {
    fn lower_alnum(c: u8) -> bool {
        c.is_ascii_lowercase() || c.is_ascii_digit()
    }
    !value.is_empty()
        && value.split('.').all(|label| match label.as_bytes() {
            [] => false,
            [c] => lower_alnum(*c),
            [first, mid @ .., last] => {
                lower_alnum(*first)
                    && lower_alnum(*last)
                    && mid.iter().all(|&c| lower_alnum(c) || c == b'-')
            }
        })
}

/// go: validation.go `IsQualifiedName`
fn is_qualified_name(value: &str) -> Vec<String> {
    let mut errs = Vec::new();
    let parts: Vec<&str> = value.split('/').collect();
    let name = match parts.as_slice() {
        [name] => *name,
        [prefix, name] => {
            if prefix.is_empty() {
                errs.push(format!("prefix part {EMPTY_ERROR}"));
            } else {
                errs.extend(
                    is_dns1123_subdomain(prefix)
                        .into_iter()
                        .map(|msg| format!("prefix part {msg}")),
                );
            }
            *name
        }
        _ => {
            errs.push(format!(
                "a qualified name {} with an optional DNS subdomain prefix and '/' (e.g. 'example.com/MyName')",
                regex_error(QUALIFIED_NAME_ERR_MSG, QUALIFIED_NAME_FMT, &["MyName", "my.name", "123-abc"])
            ));
            return errs;
        }
    };

    if name.is_empty() {
        errs.push(format!("name part {EMPTY_ERROR}"));
    } else if name.len() > QUALIFIED_NAME_MAX_LENGTH {
        errs.push(format!(
            "name part {}",
            max_len_error(QUALIFIED_NAME_MAX_LENGTH)
        ));
    }
    // Go runs the regex check independently of the empty/max-length checks,
    // so an empty or over-long name can accumulate a second message.
    if !matches_qualified_name(name) {
        errs.push(format!(
            "name part {}",
            regex_error(
                QUALIFIED_NAME_ERR_MSG,
                QUALIFIED_NAME_FMT,
                &["MyName", "my.name", "123-abc"]
            )
        ));
    }
    errs
}

/// go: validation.go `IsValidLabelValue`
fn is_valid_label_value(value: &str) -> Vec<String> {
    let mut errs = Vec::new();
    if value.len() > LABEL_VALUE_MAX_LENGTH {
        errs.push(max_len_error(LABEL_VALUE_MAX_LENGTH));
    }
    // labelValueFmt makes the whole qualified-name group optional.
    if !(value.is_empty() || matches_qualified_name(value)) {
        errs.push(regex_error(
            LABEL_VALUE_ERR_MSG,
            LABEL_VALUE_FMT,
            &["MyValue", "my_value", "12345"],
        ));
    }
    errs
}

/// go: validation.go `IsDNS1123Subdomain`
fn is_dns1123_subdomain(value: &str) -> Vec<String> {
    let mut errs = Vec::new();
    if value.len() > DNS1123_SUBDOMAIN_MAX_LENGTH {
        errs.push(max_len_error(DNS1123_SUBDOMAIN_MAX_LENGTH));
    }
    if !matches_dns1123_subdomain(value) {
        errs.push(regex_error(
            DNS1123_SUBDOMAIN_ERR_MSG,
            DNS1123_SUBDOMAIN_FMT,
            &["example.com"],
        ));
    }
    errs
}

#[cfg(test)]
mod tests {
    use std::str::FromStr;

    use kube::core::CustomResourceExt;
    use serde_json::json;

    use super::*;
    use crate::go_duration::{HOUR, MINUTE};

    fn tags(entries: &[(&str, &str)]) -> BTreeMap<String, String> {
        entries
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    // -- ValidateLeaseTags (transliterated from lease_helpers_test.go) -------

    // go: lease_helpers_test.go:434-437 "should accept empty tags"
    #[test]
    fn tags_accepts_empty() {
        assert_eq!(validate_lease_tags(&BTreeMap::new(), 10), Ok(()));
    }

    // go: lease_helpers_test.go:439-446 "should accept valid tags"
    #[test]
    fn tags_accepts_valid() {
        let t = tags(&[("team", "devops"), ("ci-job", "12345"), ("env", "staging")]);
        assert_eq!(validate_lease_tags(&t, 10), Ok(()));
    }

    // go: lease_helpers_test.go:448-456 "should reject more than 10 tags"
    #[test]
    fn tags_rejects_more_than_ten() {
        let t: BTreeMap<String, String> = (0..11)
            .map(|i| (format!("key{i}"), "value".to_string()))
            .collect();
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("too many tags"), "{err}");
        assert_eq!(err.to_string(), "too many tags: 11 (maximum 10)");
    }

    // go: lease_helpers_test.go:458-466 "should reject tag key longer than 63 chars"
    #[test]
    fn tags_rejects_long_key() {
        let t = tags(&[(&"a".repeat(64), "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("tag key"), "{err}");
        assert!(err.to_string().contains("not a valid label key"), "{err}");
        // Full string verified against apimachinery v0.33.0 output. Only one
        // message: 64 'a's exceed the max length but still match the
        // (length-unanchored) qualified-name regex.
        assert_eq!(
            err.to_string(),
            format!(
                "tag key {:?} is not a valid label key: name part must be no more than 63 characters",
                "a".repeat(64)
            )
        );
    }

    // Not in lease_helpers_test.go. Go's IsQualifiedName runs the regex check
    // as an independent `if` after the empty/max-length else-if pair
    // (validation.go:62-69), so these keys accumulate two "; "-joined
    // messages. Expected strings captured by executing Go with apimachinery
    // v0.33.0.
    #[test]
    fn tags_rejects_empty_key_with_two_messages() {
        let t = tags(&[("", "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert_eq!(
            err.to_string(),
            "tag key \"\" is not a valid label key: name part must be non-empty; \
             name part must consist of alphanumeric characters, '-', '_' or '.', and \
             must start and end with an alphanumeric character (e.g. 'MyName',  or \
             'my.name',  or '123-abc', regex used for validation is \
             '([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]')"
        );
    }

    // Not in lease_helpers_test.go; see tags_rejects_empty_key_with_two_messages.
    #[test]
    fn tags_rejects_long_invalid_key_with_two_messages() {
        let key = format!("{}!", "a".repeat(63));
        let t = tags(&[(key.as_str(), "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert_eq!(
            err.to_string(),
            format!(
                "tag key {key:?} is not a valid label key: name part must be no more \
                 than 63 characters; name part must consist of alphanumeric \
                 characters, '-', '_' or '.', and must start and end with an \
                 alphanumeric character (e.g. 'MyName',  or 'my.name',  or '123-abc', \
                 regex used for validation is '([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]')"
            )
        );
    }

    // go: lease_helpers_test.go:468-476 "should reject tag value longer than 63 chars"
    #[test]
    fn tags_rejects_long_value() {
        let t = tags(&[("key", &"v".repeat(64))]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("tag value"), "{err}");
        assert!(err.to_string().contains("not a valid label value"), "{err}");
        assert_eq!(
            err.to_string(),
            format!(
                "tag value for key \"key\" is not a valid label value: must be no more than 63 characters"
            )
        );
    }

    // go: lease_helpers_test.go:478-485 "should reject invalid label key characters"
    #[test]
    fn tags_rejects_invalid_key_characters() {
        let t = tags(&[("invalid key!", "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("not a valid label key"), "{err}");
        // Full string verified against apimachinery v0.31 output (note the
        // Go RegexError double-space quirk before each " or ").
        assert_eq!(
            err.to_string(),
            "tag key \"invalid key!\" is not a valid label key: name part must consist of \
             alphanumeric characters, '-', '_' or '.', and must start and end with an \
             alphanumeric character (e.g. 'MyName',  or 'my.name',  or '123-abc', regex used \
             for validation is '([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]')"
        );
    }

    // go: lease_helpers_test.go:487-494 "should reject invalid label value characters"
    #[test]
    fn tags_rejects_invalid_value_characters() {
        let t = tags(&[("key", "invalid value!")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("not a valid label value"), "{err}");
        assert_eq!(
            err.to_string(),
            "tag value for key \"key\" is not a valid label value: a valid label must be an \
             empty string or consist of alphanumeric characters, '-', '_' or '.', and must \
             start and end with an alphanumeric character (e.g. 'MyValue',  or 'my_value',  or \
             '12345', regex used for validation is '(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?')"
        );
    }

    // go: lease_helpers_test.go:496-503 "should reject reserved prefix jumpstarter.dev/"
    #[test]
    fn tags_rejects_reserved_prefix_jumpstarter_dev() {
        let t = tags(&[("jumpstarter.dev/custom", "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("reserved prefix"), "{err}");
        assert_eq!(
            err.to_string(),
            "tag key \"jumpstarter.dev/custom\" must not use reserved prefix"
        );
    }

    // go: lease_helpers_test.go:505-512 "should reject reserved prefix metadata.jumpstarter.dev/"
    #[test]
    fn tags_rejects_reserved_prefix_metadata_jumpstarter_dev() {
        let t = tags(&[("metadata.jumpstarter.dev/team", "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("reserved prefix"), "{err}");
    }

    // go: lease_helpers_test.go:514-521 "should reject tag key containing slash"
    #[test]
    fn tags_rejects_key_containing_slash() {
        let t = tags(&[("team/env", "value")]);
        let err = validate_lease_tags(&t, 10).unwrap_err();
        assert!(err.to_string().contains("must not contain '/'"), "{err}");
        assert_eq!(
            err.to_string(),
            "tag key \"team/env\" must not contain '/' (use simple names without prefix)"
        );
    }

    // go: lease_helpers_test.go:523-529 "should accept exactly 10 tags"
    #[test]
    fn tags_accepts_exactly_ten() {
        let t: BTreeMap<String, String> = (0..10)
            .map(|i| (format!("key{i}"), "value".to_string()))
            .collect();
        assert_eq!(validate_lease_tags(&t, 10), Ok(()));
    }

    // go: lease_helpers_test.go:531-536 "should accept key and value of exactly 63 chars"
    #[test]
    fn tags_accepts_63_char_key_and_value() {
        let t = tags(&[(&"k".repeat(63), &"v".repeat(63))]);
        assert_eq!(validate_lease_tags(&t, 10), Ok(()));
    }

    // -- condition helpers ----------------------------------------------------

    fn lease(name: &str) -> Lease {
        let mut lease = Lease::new(
            name,
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: "test-client".into(),
                },
                duration: Some(GoDuration(HOUR)),
                ..Default::default()
            },
        );
        lease.metadata.generation = Some(3);
        lease
    }

    #[test]
    fn set_status_condition_upserts_and_preserves_transition_time() {
        let mut l = lease("cond");

        l.set_status_pending("NoExporters", "no exporters available");
        let first = l.status.as_ref().unwrap().conditions[0].clone();
        assert_eq!(first.type_, "Pending");
        assert_eq!(first.status, "True");
        assert_eq!(first.reason, "NoExporters");
        assert_eq!(first.message, "no exporters available");
        assert_eq!(first.observed_generation, Some(3));

        // Same status, new reason/message: lastTransitionTime must not move
        // (meta.SetStatusCondition semantics).
        l.set_status_pending("StillWaiting", "still waiting");
        let conditions = &l.status.as_ref().unwrap().conditions;
        assert_eq!(conditions.len(), 1);
        assert_eq!(conditions[0].reason, "StillWaiting");
        assert_eq!(conditions[0].message, "still waiting");
        assert_eq!(
            conditions[0].last_transition_time, first.last_transition_time,
            "lastTransitionTime moved without a status flip"
        );

        // Status flip: lastTransitionTime is restamped.
        l.set_status_condition(LeaseConditionType::Pending, false, "Acquired", "acquired");
        let conditions = &l.status.as_ref().unwrap().conditions;
        assert_eq!(conditions[0].status, "False");
        assert!(conditions[0].last_transition_time >= first.last_transition_time);

        // Different type appends.
        l.set_status_ready(true, "Ready", "ready");
        assert_eq!(l.status.as_ref().unwrap().conditions.len(), 2);
    }

    #[test]
    fn release_and_expire_set_ready_false_ended_and_end_time() {
        let mut l = lease("release");
        l.release();
        let status = l.status.as_ref().unwrap();
        assert!(status.ended);
        assert!(status.end_time.is_some());
        let ready = status
            .conditions
            .iter()
            .find(|c| c.type_ == "Ready")
            .unwrap();
        assert_eq!(ready.status, "False");
        assert_eq!(ready.reason, "Released");
        assert_eq!(ready.message, "The lease was marked for release");

        let mut l = lease("expire");
        l.expire();
        let status = l.status.as_ref().unwrap();
        assert!(status.ended);
        assert!(status.end_time.is_some());
        let ready = status
            .conditions
            .iter()
            .find(|c| c.type_ == "Ready")
            .unwrap();
        assert_eq!(ready.status, "False");
        assert_eq!(ready.reason, "Expired");
        assert_eq!(ready.message, "The lease has expired");
    }

    #[test]
    fn exporter_and_client_names() {
        let mut l = lease("names");
        // go: lease_helpers.go:387-390 — nil ExporterRef reads as "(none)".
        assert_eq!(l.get_exporter_name(), "(none)");
        assert_eq!(l.get_client_name(), "test-client");

        l.status = Some(LeaseStatus {
            exporter_ref: Some(LocalObjectReference {
                name: "exporter-1".into(),
            }),
            ..Default::default()
        });
        assert_eq!(l.get_exporter_name(), "exporter-1");
    }

    #[test]
    fn exporter_selector_from_match_labels() {
        let mut l = lease("selector");
        l.spec.selector = LabelSelector {
            match_labels: Some([("board-type".to_string(), "qc8775".to_string())].into()),
            match_expressions: None,
        };
        let selector = l.get_exporter_selector().unwrap();
        assert_eq!(selector.to_string(), "board-type=qc8775");
    }

    // -- serde round trip -----------------------------------------------------

    /// Builds a Lease with every spec/status field populated.
    fn fully_populated_lease() -> Lease {
        let ts = |s: &str| Time(Timestamp::from_str(s).unwrap());
        let mut lease = Lease::new(
            "test-lease",
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: "test-client".into(),
                },
                duration: Some(GoDuration(HOUR + 30 * MINUTE)),
                selector: LabelSelector {
                    match_labels: Some([("board".to_string(), "rpi4".to_string())].into()),
                    match_expressions: None,
                },
                exporter_ref: Some(LocalObjectReference {
                    name: "device-1".into(),
                }),
                tags: Some(tags(&[("team", "devops"), ("ci-job", "12345")])),
                release: true,
                begin_time: Some(ts("2026-01-02T03:04:05Z")),
                end_time: Some(ts("2026-01-02T04:34:05Z")),
            },
        );
        lease.metadata.namespace = Some("default".into());
        lease.metadata.generation = Some(2);
        lease.status = Some(LeaseStatus {
            begin_time: Some(ts("2026-01-02T03:04:06Z")),
            end_time: Some(ts("2026-01-02T04:00:00Z")),
            exporter_ref: Some(LocalObjectReference {
                name: "device-1".into(),
            }),
            ended: true,
            priority: 5,
            spot_access: true,
            conditions: vec![Condition {
                type_: "Ready".into(),
                status: "False".into(),
                observed_generation: Some(2),
                last_transition_time: ts("2026-01-02T04:00:00Z"),
                reason: "Expired".into(),
                message: "The lease has expired".into(),
            }],
        });
        lease
    }

    #[test]
    fn serde_round_trip_fully_populated() {
        let lease = fully_populated_lease();
        let value = serde_json::to_value(&lease).unwrap();

        // Wire shape mirrors the Go json tags exactly.
        assert_eq!(value["apiVersion"], "jumpstarter.dev/v1alpha1");
        assert_eq!(value["kind"], "Lease");
        assert_eq!(value["spec"]["clientRef"]["name"], "test-client");
        assert_eq!(value["spec"]["duration"], "1h30m0s");
        assert_eq!(value["spec"]["selector"]["matchLabels"]["board"], "rpi4");
        assert_eq!(value["spec"]["exporterRef"]["name"], "device-1");
        assert_eq!(value["spec"]["tags"]["ci-job"], "12345");
        assert_eq!(value["spec"]["release"], true);
        assert_eq!(value["spec"]["beginTime"], "2026-01-02T03:04:05Z");
        assert_eq!(value["spec"]["endTime"], "2026-01-02T04:34:05Z");
        assert_eq!(value["status"]["beginTime"], "2026-01-02T03:04:06Z");
        assert_eq!(value["status"]["endTime"], "2026-01-02T04:00:00Z");
        assert_eq!(value["status"]["exporterRef"]["name"], "device-1");
        assert_eq!(value["status"]["ended"], true);
        assert_eq!(value["status"]["priority"], 5);
        assert_eq!(value["status"]["spotAccess"], true);
        assert_eq!(value["status"]["conditions"][0]["type"], "Ready");
        assert_eq!(
            value["status"]["conditions"][0]["lastTransitionTime"],
            "2026-01-02T04:00:00Z"
        );

        let back: Lease = serde_json::from_value(value).unwrap();
        assert_eq!(back, lease);
    }

    #[test]
    fn serde_omitempty_fields_are_absent_and_default() {
        // Zero-ish values serialize absent, exactly like Go omitempty.
        let lease = Lease::new(
            "minimal",
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: "client".into(),
                },
                ..Default::default()
            },
        );
        let value = serde_json::to_value(&lease).unwrap();
        let spec = value["spec"].as_object().unwrap();
        for absent in [
            "duration",
            "exporterRef",
            "tags",
            "release",
            "beginTime",
            "endTime",
        ] {
            assert!(
                !spec.contains_key(absent),
                "spec.{absent} should be omitted"
            );
        }
        // selector has no omitempty in Go: it always serializes.
        assert_eq!(value["spec"]["selector"], json!({}));
        // status is omitted while None.
        assert!(value.get("status").is_none());

        // Absent optional fields deserialize to their Go zero values.
        let parsed: Lease = serde_yaml_ng::from_str(
            r#"
apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  name: from-yaml
  namespace: default
spec:
  clientRef:
    name: client
  duration: 30m
  selector:
    matchLabels:
      board-type: qc8775
status:
  ended: false
"#,
        )
        .unwrap();
        assert_eq!(parsed.spec.duration, Some(GoDuration(30 * MINUTE)));
        assert!(!parsed.spec.release);
        assert_eq!(parsed.spec.tags, None);
        let status = parsed.status.unwrap();
        assert!(!status.ended);
        assert_eq!(status.priority, 0);
        assert!(!status.spot_access);
        assert!(status.conditions.is_empty());
    }

    // -- CRD schema spot checks (full structural diff lives in tests/crd_parity.rs)

    #[test]
    fn crd_matches_golden_shape() {
        let crd = serde_json::to_value(Lease::crd()).unwrap();

        assert_eq!(crd["spec"]["group"], "jumpstarter.dev");
        assert_eq!(crd["spec"]["scope"], "Namespaced");
        assert_eq!(crd["spec"]["names"]["kind"], "Lease");
        assert_eq!(crd["spec"]["names"]["plural"], "leases");
        assert_eq!(crd["spec"]["names"]["singular"], "lease");

        let version = &crd["spec"]["versions"][0];
        assert_eq!(version["name"], "v1alpha1");
        assert_eq!(version["served"], true);
        assert_eq!(version["storage"], true);
        assert_eq!(version["subresources"], json!({ "status": {} }));
        assert_eq!(
            version["additionalPrinterColumns"],
            json!([
                { "jsonPath": ".status.ended", "name": "Ended", "type": "boolean" },
                { "jsonPath": ".spec.clientRef.name", "name": "Client", "type": "string" },
                { "jsonPath": ".status.exporterRef.name", "name": "Exporter", "type": "string" },
            ])
        );

        let spec_schema = &version["schema"]["openAPIV3Schema"]["properties"]["spec"];

        // CEL rules verbatim from lease_types.go:24-25 / the golden CRD.
        assert_eq!(
            spec_schema["x-kubernetes-validations"],
            json!([
                {
                    "message": "one of selector or exporterRef.name is required",
                    "rule": "((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0)) || (has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0)",
                },
                {
                    "message": "tags are immutable after creation",
                    "rule": "!has(oldSelf.tags) || self.tags == oldSelf.tags",
                },
            ])
        );

        let props = &spec_schema["properties"];
        assert_eq!(props["duration"]["type"], "string");
        assert!(props["duration"].get("format").is_none());
        assert_eq!(props["tags"]["maxProperties"], 10);
        assert_eq!(props["tags"]["type"], "object");
        assert_eq!(
            props["tags"]["additionalProperties"],
            json!({ "type": "string" })
        );
        assert_eq!(props["selector"]["default"], json!({}));
        assert_eq!(props["release"]["type"], "boolean");
        assert!(props["release"].get("default").is_none());
        assert_eq!(props["beginTime"]["type"], "string");
        assert_eq!(props["beginTime"]["format"], "date-time");

        // Required spec properties match the golden CRD: clientRef (non-Option
        // field) plus the defaulted `selector` re-added by `require_selector`.
        assert_eq!(spec_schema["required"], json!(["clientRef", "selector"]));

        let status_schema = &version["schema"]["openAPIV3Schema"]["properties"]["status"];
        assert_eq!(status_schema["required"], json!(["ended"]));
        assert_eq!(status_schema["properties"]["priority"]["type"], "integer");
        assert!(
            status_schema["properties"]["priority"]
                .get("format")
                .is_none(),
            "Go `int` emits no format: {:?}",
            status_schema["properties"]["priority"]
        );
        assert_eq!(status_schema["properties"]["spotAccess"]["type"], "boolean");
        assert_eq!(status_schema["properties"]["conditions"]["type"], "array");
    }
}
