//! The `Client` custom resource of the `jumpstarter.dev/v1alpha1` API group.
//!
//! Ported from `controller/api/v1alpha1/client_types.go` and
//! `client_helpers.go` (behavioral reference). The Go `ClientList` type has no
//! Rust counterpart: kube-rs models lists generically via
//! `kube::core::ObjectList<Client>`.
//!
//! TODO(controller-service phase): proto conversions for the Client are
//! deferred. Go currently defines none for this type (unlike
//! `Exporter::ToProtobuf` in `exporter_helpers.go`); if the ClientService port
//! grows one, it belongs next to the service code, not here.

use std::collections::BTreeMap;

use k8s_openapi::api::core::v1::LocalObjectReference;
use kube::CustomResource;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// AnnotationMigratedNamespace is the annotation key for migrated namespace
///
/// Ported from `controller/api/v1alpha1/groupversion_info.go`. Shared with the
/// Exporter helpers in Go; kept here until a dedicated shared module exists.
pub const ANNOTATION_MIGRATED_NAMESPACE: &str = "jumpstarter.dev/migrated-namespace";

/// AnnotationMigratedUID is the annotation key for migrated UID
///
/// Ported from `controller/api/v1alpha1/groupversion_info.go`. Shared with the
/// Exporter helpers in Go; kept here until a dedicated shared module exists.
pub const ANNOTATION_MIGRATED_UID: &str = "jumpstarter.dev/migrated-uid";

/// ClientSpec defines the desired state of Client.
// The Client in the Jumpstarter controller represents a user that can access
// the Jumpstarter Controller. Clients can be associated to external identity
// OIDC providers by providing Username, i.e. Spec.Username:
// "oidc:user@example.com" (comment carried from the Go `Client` struct body).
#[derive(CustomResource, Serialize, Deserialize, Clone, Debug, Default, PartialEq, JsonSchema)]
#[kube(
    group = "jumpstarter.dev",
    version = "v1alpha1",
    kind = "Client",
    namespaced,
    status = "ClientStatus",
    derive = "Default",
    derive = "PartialEq",
    doc = "Client is the Schema for the clients API",
    // Tolerate a spec-less object (Go `json:"spec,omitempty"`) by defaulting
    // the spec; the schemars transform strips the resulting `spec.default` so
    // `::crd()` is unchanged. See `crate::schema::strip_spec_default`.
    attr = "cfg_attr(all(), serde(default))",
    attr = "cfg_attr(all(), schemars(transform = crate::schema::strip_spec_default))"
)]
#[serde(rename_all = "camelCase")]
pub struct ClientSpec {
    /// Username is the identity of the client, used for authentication and authorization.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub username: Option<String>,
}

/// ClientStatus defines the observed state of Client.
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, JsonSchema)]
#[serde(rename_all = "camelCase")]
pub struct ClientStatus {
    /// Credential is a reference to the secret containing the client credentials.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(transform = crate::schema::local_object_reference)]
    pub credential: Option<LocalObjectReference>,
    /// Endpoint is the controller gRPC endpoint URL assigned to this client.
    ///
    /// Mirrors the Go non-pointer `string` with `omitempty`: an empty value is
    /// omitted when serializing and an absent field deserializes to `""`
    /// (empty and absent are indistinguishable, exactly as in Go).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub endpoint: String,
}

impl Client {
    /// Returns the internal subject identifying this client:
    /// `client:<namespace>:<name>:<uid>`, honoring the migration annotations.
    ///
    /// Ported from `Client.InternalSubject` in
    /// `controller/api/v1alpha1/client_helpers.go`.
    pub fn internal_subject(&self) -> String {
        let (namespace, uid) = namespace_and_uid(
            self.metadata.namespace.as_deref().unwrap_or(""),
            self.metadata.uid.as_deref().unwrap_or(""),
            self.metadata.annotations.as_ref(),
        );
        let name = self.metadata.name.as_deref().unwrap_or("");
        ["client", namespace.as_str(), name, uid.as_str()].join(":")
    }

    /// Returns the accepted usernames for this client: the prefixed internal
    /// subject, plus `spec.username` when set.
    ///
    /// Ported from `Client.Usernames` in
    /// `controller/api/v1alpha1/client_helpers.go`.
    pub fn usernames(&self, prefix: &str) -> Vec<String> {
        let mut usernames = vec![format!("{prefix}{}", self.internal_subject())];

        if let Some(username) = &self.spec.username {
            usernames.push(username.clone());
        }

        usernames
    }
}

/// getNamespaceAndUID returns the namespace and UID for an object, applying migration
/// annotation overrides if present.
///
/// Ported from `controller/api/v1alpha1/common_helpers.go`. Private for now;
/// the Exporter helpers use the same logic in Go and are a consolidation
/// candidate once both modules are ported.
fn namespace_and_uid(
    namespace: &str,
    uid: &str,
    annotations: Option<&BTreeMap<String, String>>,
) -> (String, String) {
    let mut result_namespace = namespace;
    let mut result_uid = uid;

    if let Some(annotations) = annotations {
        if let Some(migrated_namespace) = annotations.get(ANNOTATION_MIGRATED_NAMESPACE) {
            if !migrated_namespace.is_empty() {
                result_namespace = migrated_namespace;
            }
        }
        if let Some(migrated_uid) = annotations.get(ANNOTATION_MIGRATED_UID) {
            if !migrated_uid.is_empty() {
                result_uid = migrated_uid;
            }
        }
    }

    (result_namespace.to_owned(), result_uid.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a Client with the given metadata, mirroring the Go test fixtures.
    fn client(
        name: &str,
        namespace: &str,
        uid: &str,
        annotations: Option<BTreeMap<String, String>>,
        spec: ClientSpec,
    ) -> Client {
        let mut client = Client::new(name, spec);
        client.metadata.namespace = Some(namespace.to_owned());
        client.metadata.uid = Some(uid.to_owned());
        client.metadata.annotations = annotations;
        client
    }

    // Transliterated from TestClient_InternalSubject in
    // `controller/api/v1alpha1/client_helpers_test.go`.

    #[test]
    fn internal_subject_without_annotations() {
        let c = client(
            "my-client",
            "default",
            "123e4567-e89b-12d3-a456-426614174000",
            None,
            ClientSpec::default(),
        );
        assert_eq!(
            c.internal_subject(),
            "client:default:my-client:123e4567-e89b-12d3-a456-426614174000"
        );
    }

    #[test]
    fn internal_subject_with_both_migrated_annotations() {
        let annotations = BTreeMap::from([
            (
                ANNOTATION_MIGRATED_NAMESPACE.to_owned(),
                "old-namespace".to_owned(),
            ),
            (
                ANNOTATION_MIGRATED_UID.to_owned(),
                "old-uid-value".to_owned(),
            ),
        ]);
        let c = client(
            "my-client",
            "default",
            "123e4567-e89b-12d3-a456-426614174000",
            Some(annotations),
            ClientSpec::default(),
        );
        assert_eq!(
            c.internal_subject(),
            "client:old-namespace:my-client:old-uid-value"
        );
    }

    #[test]
    fn internal_subject_empty_annotation_values_are_ignored() {
        let annotations = BTreeMap::from([
            (ANNOTATION_MIGRATED_NAMESPACE.to_owned(), String::new()),
            (ANNOTATION_MIGRATED_UID.to_owned(), String::new()),
        ]);
        let c = client(
            "my-client",
            "default",
            "123e4567-e89b-12d3-a456-426614174000",
            Some(annotations),
            ClientSpec::default(),
        );
        assert_eq!(
            c.internal_subject(),
            "client:default:my-client:123e4567-e89b-12d3-a456-426614174000"
        );
    }

    // Transliterated from TestClient_Usernames in
    // `controller/api/v1alpha1/client_helpers_test.go`.

    #[test]
    fn usernames_without_custom_username() {
        let c = client("my-client", "default", "123", None, ClientSpec::default());
        let got = c.usernames("internal:");
        assert_eq!(got, vec!["internal:client:default:my-client:123"]);
    }

    #[test]
    fn usernames_with_custom_username() {
        let c = client(
            "my-client",
            "default",
            "123",
            None,
            ClientSpec {
                username: Some("custom-user".to_owned()),
            },
        );
        let got = c.usernames("internal:");
        assert_eq!(
            got,
            vec![
                "internal:client:default:my-client:123".to_owned(),
                "custom-user".to_owned(),
            ]
        );
    }

    #[test]
    fn serde_round_trip() {
        let mut c = client(
            "my-client",
            "default",
            "123e4567-e89b-12d3-a456-426614174000",
            None,
            ClientSpec {
                username: Some("oidc:user@example.com".to_owned()),
            },
        );
        c.status = Some(ClientStatus {
            credential: Some(LocalObjectReference {
                name: "my-client-credential".to_owned(),
            }),
            endpoint: "grpc.jumpstarter.example.com:8082".to_owned(),
        });

        let value = serde_json::to_value(&c).expect("serialize Client");
        assert_eq!(value["apiVersion"], "jumpstarter.dev/v1alpha1");
        assert_eq!(value["kind"], "Client");
        assert_eq!(value["spec"]["username"], "oidc:user@example.com");
        assert_eq!(
            value["status"]["credential"]["name"],
            "my-client-credential"
        );
        assert_eq!(
            value["status"]["endpoint"],
            "grpc.jumpstarter.example.com:8082"
        );

        let back: Client = serde_json::from_value(value).expect("deserialize Client");
        assert_eq!(back, c);
    }

    /// The Go `endpoint` field is a non-pointer string with `omitempty`: empty
    /// serializes as absent, absent deserializes as empty.
    #[test]
    fn endpoint_empty_vs_absent() {
        let status = ClientStatus::default();
        let value = serde_json::to_value(&status).expect("serialize ClientStatus");
        assert_eq!(value, serde_json::json!({}));

        let back: ClientStatus =
            serde_json::from_value(serde_json::json!({})).expect("deserialize empty ClientStatus");
        assert_eq!(back.endpoint, "");
        assert_eq!(back.credential, None);
    }

    /// Unset spec.username serializes as absent (`*string` + `omitempty` in Go).
    #[test]
    fn username_absent_when_unset() {
        let value = serde_json::to_value(ClientSpec::default()).expect("serialize ClientSpec");
        assert_eq!(value, serde_json::json!({}));
    }

    /// Sanity-checks the CRD facts controlled by the `#[kube(...)]` attributes
    /// against `jumpstarter.dev_clients.yaml` (kubebuilder markers in
    /// `client_types.go`: object:root + subresource:status only). Full schema
    /// parity is covered by the `crd_parity` integration harness.
    #[test]
    fn crd_shape() {
        use kube::CustomResourceExt;

        let crd = Client::crd();
        assert_eq!(
            crd.metadata.name.as_deref(),
            Some("clients.jumpstarter.dev")
        );
        assert_eq!(crd.spec.group, "jumpstarter.dev");
        assert_eq!(crd.spec.scope, "Namespaced");
        assert_eq!(crd.spec.names.kind, "Client");
        assert_eq!(crd.spec.names.plural, "clients");
        assert_eq!(crd.spec.names.singular.as_deref(), Some("client"));

        let version = &crd.spec.versions[0];
        assert_eq!(crd.spec.versions.len(), 1);
        assert_eq!(version.name, "v1alpha1");
        assert!(version.served);
        assert!(version.storage);
        // +kubebuilder:subresource:status, and nothing else.
        let subresources = version.subresources.as_ref().expect("status subresource");
        assert!(subresources.status.is_some());
        assert!(subresources.scale.is_none());
        // No printer columns on the Client CRD (kube-derive emits `Some([])`
        // where controller-gen omits the key; both mean "none").
        assert!(version
            .additional_printer_columns
            .as_deref()
            .unwrap_or_default()
            .is_empty());
    }

    /// Parses `controller/config/samples/v1alpha1_client.yaml` in-place from
    /// the retained Go tree.
    #[test]
    fn parses_go_sample_manifest() {
        let yaml = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../controller/config/samples/v1alpha1_client.yaml"
        ))
        .expect("read sample manifest");
        let c: Client = serde_yaml_ng::from_str(&yaml).expect("deserialize sample Client");
        assert_eq!(c.metadata.name.as_deref(), Some("client-sample"));
        assert_eq!(c.spec, ClientSpec::default());
        assert_eq!(c.status, None);
    }
}
