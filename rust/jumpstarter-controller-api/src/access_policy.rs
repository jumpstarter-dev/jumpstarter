//! The `ExporterAccessPolicy` CRD, ported from
//! `controller/api/v1alpha1/exporteraccesspolicy_types.go`.
//!
//! ExporterAccessPolicy is the Schema for the exporteraccesspolicies API.
//! ExporterAccessPolicies are used to define the access policies for the
//! exporters. They help organize, prioritize and restrict access to the
//! exporters by clients.
//!
//! Serde mirrors the Go json tags exactly. Go fields tagged `omitempty` need
//! per-kind care because encoding/json's notion of "empty" differs by kind:
//!
//! - structs are never "empty" → Go always serializes them; mirrored as a plain
//!   field plus `#[serde(default)]` (absent still deserializes)
//! - scalars and slices are "empty" at their zero value → omitted; mirrored as
//!   a plain field plus `#[serde(default, skip_serializing_if = ...)]`, which
//!   round-trips byte-identically with Go (absent ⇄ zero value)
//! - pointers are omitted when nil → `Option<T>` plus
//!   `#[serde(skip_serializing_if = "Option::is_none")]`

use k8s_openapi::apimachinery::pkg::apis::meta::v1::LabelSelector;
use kube::CustomResource;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::go_duration::GoDuration;

/// From defines a source matcher for clients allowed access.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct From {
    /// ClientSelector is a label selector that matches clients this rule applies to.
    // Go: non-pointer `metav1.LabelSelector` with `omitempty` — encoding/json never
    // treats a struct as empty, so Go always serializes `clientSelector`.
    // The transform also drops the `default: {}` schemars derives from
    // `#[serde(default)]`: the Go field has no `+kubebuilder:default` marker.
    #[serde(default)]
    #[schemars(transform = crate::schema::label_selector)]
    pub client_selector: LabelSelector,
}

/// Policy defines an access policy rule for exporter access.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct Policy {
    /// Description is a human-readable explanation of this policy rule.
    // Go: `string` with `omitempty` — "" is omitted on the wire.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,

    /// Priority is the priority of this policy rule. Higher values indicate higher priority.
    // Go: `int` with `omitempty` — 0 is omitted on the wire. controller-gen maps Go
    // `int` to a bare `type: integer` with no `format` in the CRD schema, while
    // schemars stamps `format: int64` on i64; `schema_with` pins the golden shape.
    #[serde(default, skip_serializing_if = "is_zero")]
    #[schemars(schema_with = "go_int_schema")]
    pub priority: i64,

    /// From is the list of client selectors that this policy applies to.
    // Go: `[]From` with `omitempty` — nil and empty slices are both omitted.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub from: Vec<From>,

    /// MaximumDuration is the maximum lease duration allowed by this policy.
    // Go: `*metav1.Duration` with `omitempty` — nil is omitted; the value is a Go
    // duration string on the wire (`type: string` in the CRD schema).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub maximum_duration: Option<GoDuration>,

    /// SpotAccess indicates whether spot access (preemptible leases) is allowed.
    // Go: `bool` with `omitempty` — false is omitted on the wire.
    #[serde(default, skip_serializing_if = "is_false")]
    pub spot_access: bool,
}

/// ExporterAccessPolicySpec defines the desired state of ExporterAccessPolicy.
#[derive(CustomResource, Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[kube(
    group = "jumpstarter.dev",
    version = "v1alpha1",
    kind = "ExporterAccessPolicy",
    plural = "exporteraccesspolicies",
    namespaced,
    status = "ExporterAccessPolicyStatus",
    doc = "ExporterAccessPolicy is the Schema for the exporteraccesspolicies API.",
    derive = "Default",
    derive = "PartialEq",
    // Tolerate a spec-less object (Go `json:"spec,omitempty"`) by defaulting
    // the spec; the schemars transform strips the resulting `spec.default` so
    // `::crd()` is unchanged. See `crate::schema::strip_spec_default`.
    attr = "cfg_attr(all(), serde(default))",
    attr = "cfg_attr(all(), schemars(transform = crate::schema::strip_spec_default))"
)]
#[serde(rename_all = "camelCase")]
pub struct ExporterAccessPolicySpec {
    /// ExporterSelector is a label selector that matches the exporters this policy applies to.
    // Go: non-pointer `metav1.LabelSelector` with `omitempty` — always serialized
    // (see the module docs on struct-typed `omitempty` fields).
    // The transform also drops the `default: {}` schemars derives from
    // `#[serde(default)]`: the Go field has no `+kubebuilder:default` marker.
    #[serde(default)]
    #[schemars(transform = crate::schema::label_selector)]
    pub exporter_selector: LabelSelector,

    /// Policies is the list of access policy rules to apply.
    // Go: `[]Policy` with `omitempty` — nil and empty slices are both omitted.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub policies: Vec<Policy>,
}

/// ExporterAccessPolicyStatus defines the observed state of ExporterAccessPolicy.
// Go: empty struct; the status subresource is still declared on the CRD
// (`+kubebuilder:subresource:status`), which `#[kube(status = ...)]` mirrors.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ExporterAccessPolicyStatus {}

/// `skip_serializing_if` helper mirroring Go `omitempty` on an `int`.
fn is_zero(value: &i64) -> bool {
    *value == 0
}

/// `skip_serializing_if` helper mirroring Go `omitempty` on a `bool`.
fn is_false(value: &bool) -> bool {
    !*value
}

/// Schema for a Go `int` field: controller-gen emits a bare `type: integer`
/// with no `format`, unlike schemars' `i64` (`format: int64`).
fn go_int_schema(_generator: &mut schemars::SchemaGenerator) -> schemars::Schema {
    schemars::json_schema!({ "type": "integer" })
}

#[cfg(test)]
mod tests {
    use super::*;
    use kube::CustomResourceExt;
    use serde_json::json;

    /// The golden sample manifest, read in-place from the retained Go tree so
    /// drift over there trips this suite (same pattern as `tests/crd_parity.rs`).
    fn sample_yaml() -> String {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../controller/config/samples/v1alpha1_exporteraccesspolicy.yaml"
        );
        std::fs::read_to_string(path)
            .expect("read controller/config/samples/v1alpha1_exporteraccesspolicy.yaml")
    }

    #[test]
    fn sample_yaml_deserializes_with_go_field_semantics() {
        let cr: ExporterAccessPolicy =
            serde_yaml_ng::from_str(&sample_yaml()).expect("sample must deserialize");

        assert_eq!(cr.metadata.name.as_deref(), Some("default"));
        assert_eq!(
            cr.spec
                .exporter_selector
                .match_labels
                .as_ref()
                .and_then(|labels| labels.get("dut"))
                .map(String::as_str),
            Some("fancy-hardware")
        );

        let policies = &cr.spec.policies;
        assert_eq!(policies.len(), 3);
        assert_eq!(
            policies.iter().map(|p| p.priority).collect::<Vec<_>>(),
            vec![20, 10, 5]
        );
        // spotAccess is absent on the first two policies → Go zero value (false).
        assert_eq!(
            policies.iter().map(|p| p.spot_access).collect::<Vec<_>>(),
            vec![false, false, true]
        );
        // maximumDuration is a nil pointer on the first policy, set on the others.
        assert!(policies[0].maximum_duration.is_none());
        assert!(policies[1].maximum_duration.is_some());
        assert!(policies[2].maximum_duration.is_some());
        assert_eq!(
            policies[2]
                .from
                .first()
                .and_then(|from| from.client_selector.match_labels.as_ref())
                .and_then(|labels| labels.get("client-type"))
                .map(String::as_str),
            Some("ci")
        );
    }

    #[test]
    fn sample_yaml_round_trip_reaches_a_serde_fixed_point() {
        // The sample uses the human form "24h", which Go itself re-marshals as
        // "24h0m0s" — so byte-identity with the input is not even Go behavior.
        // The contract is a fixed point: serialize → deserialize → serialize
        // must be stable.
        let cr: ExporterAccessPolicy =
            serde_yaml_ng::from_str(&sample_yaml()).expect("sample must deserialize");
        let first = serde_json::to_value(&cr).expect("serialize");
        let reparsed: ExporterAccessPolicy =
            serde_json::from_value(first.clone()).expect("re-deserialize");
        let second = serde_json::to_value(&reparsed).expect("re-serialize");
        assert_eq!(first, second);
        assert_eq!(cr, reparsed);

        // The Go duration survives as a wire string.
        assert!(first["spec"]["policies"][1]["maximumDuration"].is_string());
    }

    #[test]
    fn canonical_json_round_trips_byte_identically() {
        // Durations in the canonical Go marshaling form ("24h0m0s"), so the
        // full value — not just a fixed point — must round-trip unchanged.
        let original = json!({
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "ExporterAccessPolicy",
            "metadata": { "name": "default", "namespace": "jumpstarter-lab" },
            "spec": {
                "exporterSelector": {
                    "matchLabels": { "dut": "fancy-hardware" },
                    "matchExpressions": [
                        { "key": "board", "operator": "In", "values": ["rpi4", "rpi5"] }
                    ]
                },
                "policies": [
                    {
                        "description": "Developers — maximum 24h lease duration",
                        "priority": 10,
                        "maximumDuration": "24h0m0s",
                        "spotAccess": true,
                        "from": [
                            { "clientSelector": { "matchLabels": { "client-type": "developer" } } }
                        ]
                    }
                ]
            }
        });
        let cr: ExporterAccessPolicy =
            serde_json::from_value(original.clone()).expect("deserialize");
        let output = serde_json::to_value(&cr).expect("serialize");
        assert_eq!(output, original);
    }

    #[test]
    fn omitempty_zero_values_serialize_like_go() {
        // Go marshals zero-valued omitempty scalars/slices/pointers as absent,
        // but always emits struct-typed fields — `Policy{}` → `{}` and
        // `ExporterAccessPolicySpec{}` → `{"exporterSelector":{}}`.
        assert_eq!(serde_json::to_value(Policy::default()).unwrap(), json!({}));
        assert_eq!(
            serde_json::to_value(From::default()).unwrap(),
            json!({ "clientSelector": {} })
        );
        assert_eq!(
            serde_json::to_value(ExporterAccessPolicySpec::default()).unwrap(),
            json!({ "exporterSelector": {} })
        );
        assert_eq!(
            serde_json::to_value(ExporterAccessPolicyStatus::default()).unwrap(),
            json!({})
        );
    }

    #[test]
    fn omitempty_fields_deserialize_from_absent_keys() {
        // Absent keys deserialize to the Go zero values (K8s pruning/patch shape).
        let policy: Policy = serde_json::from_value(json!({})).expect("empty policy");
        assert_eq!(policy, Policy::default());

        let spec: ExporterAccessPolicySpec = serde_json::from_value(json!({})).expect("empty spec");
        assert_eq!(spec, ExporterAccessPolicySpec::default());
        assert_eq!(spec.exporter_selector, LabelSelector::default());
        assert!(spec.policies.is_empty());
    }

    #[test]
    fn crd_matches_golden_identity_and_schema_shapes() {
        let crd = serde_json::to_value(ExporterAccessPolicy::crd()).expect("crd");

        // Identity block, per the golden
        // controller/deploy/operator/config/crd/bases/jumpstarter.dev_exporteraccesspolicies.yaml.
        assert_eq!(crd["spec"]["group"], "jumpstarter.dev");
        assert_eq!(crd["spec"]["scope"], "Namespaced");
        assert_eq!(crd["spec"]["names"]["kind"], "ExporterAccessPolicy");
        // NB: kube-derive does not populate `names.listKind` (the API server
        // defaults it to `<kind>List`); the golden YAML spells it out. That
        // delta belongs to the crd_parity normalization, not this type.
        assert_eq!(crd["spec"]["names"]["plural"], "exporteraccesspolicies");
        assert_eq!(crd["spec"]["names"]["singular"], "exporteraccesspolicy");

        let version = &crd["spec"]["versions"][0];
        assert_eq!(version["name"], "v1alpha1");
        assert_eq!(version["served"], true);
        assert_eq!(version["storage"], true);
        // +kubebuilder:subresource:status
        assert_eq!(version["subresources"], json!({ "status": {} }));

        let policy_schema = &version["schema"]["openAPIV3Schema"]["properties"]["spec"]
            ["properties"]["policies"]["items"]["properties"];
        // Go `int` → bare integer with no format (golden shape).
        assert_eq!(policy_schema["priority"]["type"], "integer");
        assert_eq!(policy_schema["priority"].get("format"), None);
        // *metav1.Duration → plain string.
        assert_eq!(policy_schema["maximumDuration"]["type"], "string");
        assert_eq!(policy_schema["spotAccess"]["type"], "boolean");
        assert_eq!(policy_schema["description"]["type"], "string");
        assert_eq!(policy_schema["from"]["type"], "array");
        assert_eq!(
            policy_schema["from"]["items"]["properties"]["clientSelector"]["type"],
            "object"
        );

        // Empty Go status struct still yields an object schema.
        let status_schema = &version["schema"]["openAPIV3Schema"]["properties"]["status"];
        assert_eq!(status_schema["type"], "object");
    }
}
