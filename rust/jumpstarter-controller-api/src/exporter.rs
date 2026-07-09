//! The `Exporter` custom resource, ported from
//! `controller/api/v1alpha1/exporter_types.go` and
//! `controller/api/v1alpha1/exporter_helpers.go`.
//!
//! TODO(phase 5): port `Exporter::ToProtobuf` / `ExporterList::ToProtobuf`
//! (`cpb.Exporter` mapping, including the deprecated `Online` flag derived
//! from conditions and `UnparseExporterIdentifier` name formatting) once
//! `jumpstarter-protocol` client types are wired into the controller crates.

use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, Time};
use kube::CustomResource;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::device::Device;
use crate::labels::namespace_and_uid;

/// ExporterSpec defines the desired state of Exporter.
///
/// Exporters represent the services that connect to the physical or virtual
/// devices. They are responsible for providing the access to the devices and
/// for the communication with the devices. A jumpstarter exporter service
/// should be run on a linux machine, or a pod, with the exporter credentials
/// and the right configuration for this resource to become online. For
/// more information see the Jumpstarter documentation:
/// <https://jumpstarter.dev/main/introduction/exporters.html#exporters>
// go: exporter_types.go:25 (ExporterSpec), exporter_types.go:70-89 (Exporter markers)
#[derive(CustomResource, Serialize, Deserialize, Clone, Debug, Default, PartialEq, JsonSchema)]
#[kube(
    group = "jumpstarter.dev",
    version = "v1alpha1",
    kind = "Exporter",
    namespaced,
    status = "ExporterStatus",
    derive = "Default",
    doc = "Exporter is the Schema for the exporters API",
    printcolumn = r#"{"name":"Status", "type":"string", "jsonPath":".status.exporterStatus"}"#,
    printcolumn = r#"{"name":"Message", "type":"string", "jsonPath":".status.statusMessage", "priority":1}"#,
    // Deserialize a spec-less object (Go `json:"spec,omitempty"` omits a
    // zero-value spec) as a default spec. `cfg_attr(all(), ..)` smuggles the
    // `serde`/`schemars` attrs past kube-derive's attr filter; the schemars
    // transform strips the resulting `spec.default` so `::crd()` is unchanged.
    attr = "cfg_attr(all(), serde(default))",
    attr = "cfg_attr(all(), schemars(transform = crate::schema::strip_spec_default))"
)]
#[serde(rename_all = "camelCase")]
pub struct ExporterSpec {
    /// Username is the identity of the exporter, used for authentication and authorization.
    // go: exporter_types.go:27 `json:"username,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub username: Option<String>,
}

/// ExporterStatus defines the observed state of Exporter.
// go: exporter_types.go:31 (ExporterStatus)
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct ExporterStatus {
    /// Conditions represent the latest available observations of the exporter state.
    // go: exporter_types.go:33 `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::conditions)]
    pub conditions: Option<Vec<Condition>>,
    /// Credential is a reference to the secret containing the exporter credentials.
    // go: exporter_types.go:35 `json:"credential,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::local_object_reference)]
    pub credential: Option<LocalObjectReference>,
    /// Devices is the list of driver instances currently reported by the exporter.
    // go: exporter_types.go:37 `json:"devices,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub devices: Option<Vec<Device>>,
    /// LeaseRef is a reference to the lease currently assigned to this exporter.
    // go: exporter_types.go:39 `json:"leaseRef,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::local_object_reference)]
    pub lease_ref: Option<LocalObjectReference>,
    /// LastSeen is the timestamp of the last communication from the exporter.
    ///
    /// Note: the Go field is a non-pointer `metav1.Time` whose zero value
    /// marshals to `null` (the `omitempty` tag has no effect on structs), so
    /// Go emits `"lastSeen": null` when unset while this port omits the key.
    // go: exporter_types.go:41 `json:"lastSeen,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_seen: Option<Time>,
    /// Endpoint is the gRPC endpoint URL where the exporter is reachable.
    // go: exporter_types.go:43 `json:"endpoint,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub endpoint: Option<String>,
    /// ExporterStatusValue is the current operational status reported by the exporter
    // go: exporter_types.go:46 `json:"exporterStatus,omitempty"`
    //     +kubebuilder:validation:Enum=Unspecified;Offline;Available;BeforeLeaseHook;LeaseReady;AfterLeaseHook;BeforeLeaseHookFailed;AfterLeaseHookFailed
    #[serde(rename = "exporterStatus", skip_serializing_if = "Option::is_none")]
    #[schemars(transform = exporter_status_value_schema)]
    pub exporter_status: Option<ExporterStatusValue>,
    /// StatusMessage is an optional human-readable message describing the current state
    // go: exporter_types.go:48 `json:"statusMessage,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_message: Option<String>,
}

/// ExporterStatus values - PascalCase for Kubernetes, converted from proto ALL_CAPS.
///
/// Serializes to the exact Go string constants (the variant names), and
/// schemars emits `type: string` plus these enum values in declaration order,
/// matching the kubebuilder `+kubebuilder:validation:Enum=...` marker.
// go: exporter_types.go:59-68 (ExporterStatus* constants)
#[derive(Serialize, Deserialize, Clone, Copy, Debug, Default, PartialEq, Eq, JsonSchema)]
pub enum ExporterStatusValue {
    #[default]
    Unspecified,
    Offline,
    Available,
    BeforeLeaseHook,
    LeaseReady,
    AfterLeaseHook,
    BeforeLeaseHookFailed,
    AfterLeaseHookFailed,
}

/// Field-level schema transform for `Option<ExporterStatusValue>`.
///
/// kube-derive's schema transforms would otherwise turn the derived
/// `Option<enum>` schema into `enum: [..., null]` + `nullable: true`, but
/// controller-gen emits a plain string enum for the kubebuilder
/// `+kubebuilder:validation:Enum` marker — pin that exact shape (values in
/// marker order). A transform (rather than `schema_with`) keeps schemars'
/// `Option`-based requiredness detection, so `exporterStatus` stays out of
/// the schema's `required` list, matching the Go CRD.
// go: exporter_types.go:45 (+kubebuilder:validation:Enum marker)
fn exporter_status_value_schema(schema: &mut schemars::Schema) {
    *schema = schemars::json_schema!({
        "type": "string",
        "enum": [
            "Unspecified",
            "Offline",
            "Available",
            "BeforeLeaseHook",
            "LeaseReady",
            "AfterLeaseHook",
            "BeforeLeaseHookFailed",
            "AfterLeaseHookFailed",
        ],
    });
}

impl ExporterStatusValue {
    /// The exact CRD string value for this status.
    // go: exporter_types.go:59-68
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unspecified => "Unspecified",
            Self::Offline => "Offline",
            Self::Available => "Available",
            Self::BeforeLeaseHook => "BeforeLeaseHook",
            Self::LeaseReady => "LeaseReady",
            Self::AfterLeaseHook => "AfterLeaseHook",
            Self::BeforeLeaseHookFailed => "BeforeLeaseHookFailed",
            Self::AfterLeaseHookFailed => "AfterLeaseHookFailed",
        }
    }
}

impl std::fmt::Display for ExporterStatusValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Converts a CRD string value to the status enum; any unknown or empty
/// string maps to [`ExporterStatusValue::Unspecified`], mirroring the default
/// arm of the Go conversion.
// go: exporter_helpers.go:42 (stringToProtoStatus)
impl From<&str> for ExporterStatusValue {
    fn from(state: &str) -> Self {
        match state {
            "Offline" => Self::Offline,
            "Available" => Self::Available,
            "BeforeLeaseHook" => Self::BeforeLeaseHook,
            "LeaseReady" => Self::LeaseReady,
            "AfterLeaseHook" => Self::AfterLeaseHook,
            "BeforeLeaseHookFailed" => Self::BeforeLeaseHookFailed,
            "AfterLeaseHookFailed" => Self::AfterLeaseHookFailed,
            _ => Self::Unspecified,
        }
    }
}

impl Exporter {
    /// The internal token subject for this exporter:
    /// `exporter:<namespace>:<name>:<uid>`, honoring the
    /// `jumpstarter.dev/migrated-namespace` and `jumpstarter.dev/migrated-uid`
    /// annotation overrides.
    // go: exporter_helpers.go:13 (InternalSubject)
    pub fn internal_subject(&self) -> String {
        let (namespace, uid) = namespace_and_uid(
            self.metadata.namespace.as_deref().unwrap_or_default(),
            self.metadata.uid.as_deref().unwrap_or_default(),
            self.metadata.annotations.as_ref(),
        );
        [
            "exporter",
            &namespace,
            self.metadata.name.as_deref().unwrap_or_default(),
            &uid,
        ]
        .join(":")
    }

    /// All usernames this exporter authenticates as: the prefixed internal
    /// subject, plus `spec.username` when set.
    // go: exporter_helpers.go:18 (Usernames)
    pub fn usernames(&self, prefix: &str) -> Vec<String> {
        let mut usernames = vec![format!("{prefix}{}", self.internal_subject())];

        if let Some(username) = &self.spec.username {
            usernames.push(username.clone());
        }

        usernames
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::labels::{ANNOTATION_MIGRATED_NAMESPACE, ANNOTATION_MIGRATED_UID};
    use k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta;
    use kube::CustomResourceExt;
    use serde_json::json;
    use std::collections::BTreeMap;

    const UID: &str = "123e4567-e89b-12d3-a456-426614174000";

    fn exporter(annotations: Option<BTreeMap<String, String>>) -> Exporter {
        Exporter {
            metadata: ObjectMeta {
                name: Some("my-exporter".into()),
                namespace: Some("default".into()),
                uid: Some(UID.into()),
                annotations,
                ..Default::default()
            },
            spec: ExporterSpec::default(),
            status: None,
        }
    }

    // go: exporter_helpers_test.go:11 ("without annotations")
    #[test]
    fn internal_subject_without_annotations() {
        assert_eq!(
            exporter(None).internal_subject(),
            format!("exporter:default:my-exporter:{UID}")
        );
    }

    // go: exporter_helpers_test.go:25 ("with both migrated annotations")
    #[test]
    fn internal_subject_with_both_migrated_annotations() {
        let annotations = BTreeMap::from([
            (
                ANNOTATION_MIGRATED_NAMESPACE.to_string(),
                "old-namespace".to_string(),
            ),
            (
                ANNOTATION_MIGRATED_UID.to_string(),
                "old-uid-value".to_string(),
            ),
        ]);
        assert_eq!(
            exporter(Some(annotations)).internal_subject(),
            "exporter:old-namespace:my-exporter:old-uid-value"
        );
    }

    // go: exporter_helpers_test.go:43 ("empty annotation values are ignored")
    #[test]
    fn internal_subject_empty_annotation_values_are_ignored() {
        let annotations = BTreeMap::from([
            (ANNOTATION_MIGRATED_NAMESPACE.to_string(), String::new()),
            (ANNOTATION_MIGRATED_UID.to_string(), String::new()),
        ]);
        assert_eq!(
            exporter(Some(annotations)).internal_subject(),
            format!("exporter:default:my-exporter:{UID}")
        );
    }

    // go: exporter_helpers_test.go:63 ("without custom username")
    #[test]
    fn usernames_without_custom_username() {
        let mut e = exporter(None);
        e.metadata.uid = Some("123".into());
        assert_eq!(
            e.usernames("internal:"),
            vec!["internal:exporter:default:my-exporter:123".to_string()]
        );
    }

    // go: exporter_helpers_test.go:74 ("with custom username")
    #[test]
    fn usernames_with_custom_username() {
        let mut e = exporter(None);
        e.metadata.uid = Some("123".into());
        e.spec.username = Some("custom-user".into());
        assert_eq!(
            e.usernames("internal:"),
            vec![
                "internal:exporter:default:my-exporter:123".to_string(),
                "custom-user".to_string(),
            ]
        );
    }

    // go: exporter_helpers.go:42 (stringToProtoStatus, incl. the default arm)
    #[test]
    fn status_string_conversions() {
        let all = [
            ExporterStatusValue::Unspecified,
            ExporterStatusValue::Offline,
            ExporterStatusValue::Available,
            ExporterStatusValue::BeforeLeaseHook,
            ExporterStatusValue::LeaseReady,
            ExporterStatusValue::AfterLeaseHook,
            ExporterStatusValue::BeforeLeaseHookFailed,
            ExporterStatusValue::AfterLeaseHookFailed,
        ];
        for status in all {
            // string -> enum -> string round-trip
            assert_eq!(ExporterStatusValue::from(status.as_str()), status);
            // the serde representation is the exact Go string constant
            assert_eq!(
                serde_json::to_value(status).unwrap(),
                json!(status.as_str())
            );
        }
        // unknown/empty strings map to Unspecified (Go default arm)
        assert_eq!(
            ExporterStatusValue::from("not-a-status"),
            ExporterStatusValue::Unspecified
        );
        assert_eq!(
            ExporterStatusValue::from(""),
            ExporterStatusValue::Unspecified
        );
    }

    /// Serde round-trip of a fully-populated Exporter: every spec/status
    /// field set, JSON -> Exporter -> JSON must be lossless.
    #[test]
    fn serde_round_trip_fully_populated() {
        let original = json!({
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": {
                "name": "my-exporter",
                "namespace": "default",
                "uid": UID,
                "labels": {"example.com/board": "rpi4"},
                "annotations": {
                    "jumpstarter.dev/migrated-namespace": "old-namespace",
                    "jumpstarter.dev/migrated-uid": "old-uid",
                },
            },
            "spec": {
                "username": "custom-user",
            },
            "status": {
                "conditions": [{
                    "lastTransitionTime": "2025-01-02T03:04:05Z",
                    "message": "exporter registered",
                    "observedGeneration": 3,
                    "reason": "Register",
                    "status": "True",
                    "type": "Registered",
                }],
                "credential": {"name": "my-exporter-credential"},
                "devices": [{
                    "uuid": "aaaa-bbbb",
                    "parent_uuid": "cccc-dddd",
                    "labels": {"jumpstarter.dev/client": "power"},
                }],
                "leaseRef": {"name": "01890a5d-ac96-774b-bcce-b302099afcaf"},
                "lastSeen": "2025-01-02T03:04:05Z",
                "endpoint": "grpc.jumpstarter.example.com:8082",
                "exporterStatus": "Available",
                "statusMessage": "lease ready",
            },
        });

        let parsed: Exporter = serde_json::from_value(original.clone()).unwrap();
        assert_eq!(
            parsed.status.as_ref().unwrap().exporter_status,
            Some(ExporterStatusValue::Available)
        );
        assert_eq!(serde_json::to_value(&parsed).unwrap(), original);
    }

    /// A live cluster object has NO top-level `spec` (Go declares
    /// `Spec ExporterSpec json:"spec,omitempty"`, so a zero-value spec is
    /// omitted). Deserialization — the exact path `kube::Api::get/list` take —
    /// must succeed with a default spec, matching Go's zero-value tolerance.
    #[test]
    fn deserializes_spec_less_object() {
        let spec_less = json!({
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": { "name": "power-exporter", "namespace": "jumpstarter-lab" },
            "status": { "endpoint": "127.0.0.1:8082" },
        });
        let parsed: Exporter = serde_json::from_value(spec_less).expect("spec-less Exporter");
        assert_eq!(parsed.spec, ExporterSpec::default());
        assert_eq!(
            parsed.status.and_then(|s| s.endpoint).as_deref(),
            Some("127.0.0.1:8082")
        );
    }

    /// Structural expectations on the generated CRD (the full diff against
    /// the controller-gen golden YAML lives in `tests/crd_parity.rs`).
    #[test]
    fn crd_shape() {
        let crd = serde_json::to_value(Exporter::crd()).unwrap();

        assert_eq!(crd["metadata"]["name"], "exporters.jumpstarter.dev");
        assert_eq!(crd["spec"]["group"], "jumpstarter.dev");
        assert_eq!(crd["spec"]["scope"], "Namespaced");
        assert_eq!(crd["spec"]["names"]["kind"], "Exporter");
        assert_eq!(crd["spec"]["names"]["plural"], "exporters");
        assert_eq!(crd["spec"]["names"]["singular"], "exporter");

        let version = &crd["spec"]["versions"][0];
        assert_eq!(version["name"], "v1alpha1");

        // Printer columns: Status, and Message at priority 1.
        // go: exporter_types.go:72-73 (+kubebuilder:printcolumn markers)
        let columns = version["additionalPrinterColumns"].as_array().unwrap();
        assert_eq!(columns.len(), 2);
        assert_eq!(columns[0]["name"], "Status");
        assert_eq!(columns[0]["type"], "string");
        assert_eq!(columns[0]["jsonPath"], ".status.exporterStatus");
        assert_eq!(columns[0].get("priority"), None);
        assert_eq!(columns[1]["name"], "Message");
        assert_eq!(columns[1]["type"], "string");
        assert_eq!(columns[1]["jsonPath"], ".status.statusMessage");
        assert_eq!(columns[1]["priority"], 1);

        // Status subresource is enabled.
        // go: exporter_types.go:71 (+kubebuilder:subresource:status)
        assert!(version["subresources"]["status"].is_object());

        // No status property is required (all Go fields carry omitempty).
        let status_schema = &version["schema"]["openAPIV3Schema"]["properties"]["status"];
        assert_eq!(status_schema.get("required"), None);

        // status.exporterStatus is a string with the exact enum values in
        // the kubebuilder marker's order.
        // go: exporter_types.go:45 (+kubebuilder:validation:Enum marker)
        let exporter_status = &status_schema["properties"]["exporterStatus"];
        assert_eq!(exporter_status["type"], "string");
        assert_eq!(
            exporter_status["enum"],
            json!([
                "Unspecified",
                "Offline",
                "Available",
                "BeforeLeaseHook",
                "LeaseReady",
                "AfterLeaseHook",
                "BeforeLeaseHookFailed",
                "AfterLeaseHookFailed",
            ])
        );

        // lastSeen keeps the metav1.Time shape.
        let last_seen =
            &version["schema"]["openAPIV3Schema"]["properties"]["status"]["properties"]["lastSeen"];
        assert_eq!(last_seen["type"], "string");
        assert_eq!(last_seen["format"], "date-time");
    }
}
