//! Field-level schema transforms pinning the exact controller-gen shapes for
//! embedded apimachinery types.
//!
//! controller-gen generates CRD schemas for `metav1`/`corev1` types from their
//! Go source, picking up kubebuilder validation markers (`maxLength`,
//! `pattern`, `enum`, `minimum`, defaults) and topology markers
//! (`x-kubernetes-map-type`, `x-kubernetes-list-type`). The `JsonSchema` impls
//! in k8s-openapi are derived from the published OpenAPI swagger instead,
//! which carries none of those markers — so the schemas below are pinned by
//! hand, byte-matched against the golden CRDs in
//! `controller/deploy/operator/config/crd/bases/` by `tests/crd_parity.rs`.
//!
//! Descriptions are deliberately omitted: CRD parity is structural-only by
//! decision (the harness strips descriptions from both sides).
//!
//! Replacing the whole field schema also sidesteps kube-derive's hardwired
//! `AddNullable` transform for `Option<T>` fields (the transformed schema has
//! no `null` variant left to rewrite), while schemars' `Option`-based
//! requiredness detection is unaffected — optional fields stay out of
//! `required`, exactly like the Go `omitempty` pointer fields they mirror.

use schemars::{json_schema, Schema};
use serde_json::Value;

/// Container transform for a kube-derive CR wrapper struct: removes the
/// `default: {}` that schemars emits for the `spec` property.
///
/// The four `jumpstarter.dev` kinds carry a container-level
/// `#[serde(default)]` (injected via `#[kube(attr = "cfg_attr(all(),
/// serde(default))")]`) so a **spec-less** object deserializes with a default
/// spec — Go declares every `Spec` field `json:"spec,omitempty"`, so a
/// zero-value spec is omitted from the serialized object entirely and the API
/// server stores it spec-less. Without the container default, kube-derive's
/// derived `Deserialize` errors with `missing field \`spec\``.
///
/// schemars reads that `#[serde(default)]` and stamps `default: {}` onto the
/// `spec` sub-schema, but controller-gen never emits a root-level `spec.default`
/// — so this transform strips it back out, keeping `::crd()` byte-identical to
/// the golden CRD (the CRD parity harness compares the un-normalized schema).
pub fn strip_spec_default(schema: &mut Schema) {
    if let Some(spec) = schema
        .ensure_object()
        .get_mut("properties")
        .and_then(Value::as_object_mut)
        .and_then(|props| props.get_mut("spec"))
        .and_then(Value::as_object_mut)
    {
        spec.remove("default");
    }
}

/// Pins the controller-gen schema for `corev1.LocalObjectReference`:
/// `name` is a defaulted-empty string (not required — "effectively required,
/// but allowed to be empty for backwards compatibility"), and the object is
/// `x-kubernetes-map-type: atomic`.
// go: k8s.io/api/core/v1 LocalObjectReference (+default:"" +mapType=atomic)
pub fn local_object_reference(schema: &mut Schema) {
    *schema = json_schema!({
        "type": "object",
        "properties": {
            "name": { "type": "string", "default": "" },
        },
        "x-kubernetes-map-type": "atomic",
    });
}

/// Pins the controller-gen schema for `metav1.LabelSelector`: atomic map
/// type, atomic `matchExpressions`/`values` lists, and
/// `required: [key, operator]` on each requirement.
// go: k8s.io/apimachinery/pkg/apis/meta/v1 LabelSelector (+mapType=atomic,
// +listType=atomic markers)
pub fn label_selector(schema: &mut Schema) {
    *schema = label_selector_schema();
}

/// [`label_selector`] plus the `default: {}` emitted for the kubebuilder
/// `+kubebuilder:default:={}` marker (Lease `spec.selector`).
// go: lease_types.go:35 (+kubebuilder:default:={})
pub fn label_selector_defaulted(schema: &mut Schema) {
    let mut pinned = label_selector_schema();
    pinned
        .ensure_object()
        .insert("default".into(), serde_json::json!({}));
    *schema = pinned;
}

fn label_selector_schema() -> Schema {
    json_schema!({
        "type": "object",
        "properties": {
            "matchExpressions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": { "type": "string" },
                        "operator": { "type": "string" },
                        "values": {
                            "type": "array",
                            "items": { "type": "string" },
                            "x-kubernetes-list-type": "atomic",
                        },
                    },
                    "required": ["key", "operator"],
                },
                "x-kubernetes-list-type": "atomic",
            },
            "matchLabels": {
                "type": "object",
                "additionalProperties": { "type": "string" },
            },
        },
        "x-kubernetes-map-type": "atomic",
    })
}

/// Pins the controller-gen schema for a `[]metav1.Condition` field: the
/// Condition properties carry the upstream kubebuilder validation markers
/// (`maxLength`, `minLength`, `pattern`, `enum`, `minimum`) that the
/// swagger-derived k8s-openapi schema lacks.
// go: k8s.io/apimachinery/pkg/apis/meta/v1 Condition (validation markers on
// Type/Status/ObservedGeneration/LastTransitionTime/Reason/Message)
pub fn conditions(schema: &mut Schema) {
    *schema = json_schema!({
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "lastTransitionTime": { "type": "string", "format": "date-time" },
                "message": { "type": "string", "maxLength": 32768 },
                "observedGeneration": {
                    "type": "integer",
                    "format": "int64",
                    "minimum": 0,
                },
                "reason": {
                    "type": "string",
                    "maxLength": 1024,
                    "minLength": 1,
                    "pattern": "^[A-Za-z]([A-Za-z0-9_,:]*[A-Za-z0-9_])?$",
                },
                "status": {
                    "type": "string",
                    "enum": ["True", "False", "Unknown"],
                },
                "type": {
                    "type": "string",
                    "maxLength": 316,
                    "pattern": "^([a-z0-9]([-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*/)?(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])$",
                },
            },
            "required": ["lastTransitionTime", "message", "reason", "status", "type"],
        },
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaulted_label_selector_only_adds_default() {
        let mut plain = Schema::default();
        label_selector(&mut plain);
        let mut defaulted = Schema::default();
        label_selector_defaulted(&mut defaulted);

        let mut plain = serde_json::to_value(&plain).unwrap();
        let defaulted = serde_json::to_value(&defaulted).unwrap();
        assert_eq!(plain.get("default"), None);
        plain
            .as_object_mut()
            .unwrap()
            .insert("default".into(), serde_json::json!({}));
        assert_eq!(plain, defaulted);
    }
}
