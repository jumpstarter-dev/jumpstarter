//! Spec-less deserialize parity: real cluster objects have NO top-level `spec`.
//!
//! Every `jumpstarter.dev/v1alpha1` Go type declares its spec
//! `json:"spec,omitempty"` on a non-pointer struct, so a zero-value spec is
//! omitted from the serialized object entirely and the API server stores the
//! object spec-less (its root schema does not `require` `spec`). The Rust
//! wrapper types must deserialize such objects — the exact path
//! `kube::Api::<T>::get()/list()` take (`serde_json::from_slice::<T>`) — with a
//! default spec, matching Go's zero-value tolerance. A committed live capture
//! (`fixtures/live/exporter-power-no-spec.json`) pins the Exporter case; the
//! Client and ExporterAccessPolicy cases are synthesized to the same shape.

use serde_json::json;

use jumpstarter_controller_api::access_policy::{ExporterAccessPolicy, ExporterAccessPolicySpec};
use jumpstarter_controller_api::client::{Client, ClientSpec};
use jumpstarter_controller_api::exporter::{Exporter, ExporterSpec, ExporterStatusValue};

/// The committed live capture of a spec-less Exporter (root keys:
/// apiVersion/kind/metadata/status — no `spec`).
#[test]
fn exporter_live_fixture_deserializes_spec_less() {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/live/exporter-power-no-spec.json"
    ))
    .expect("read exporter-power-no-spec.json fixture");

    // Sanity: the fixture really has no top-level `spec` key.
    let value: serde_json::Value = serde_json::from_str(&raw).expect("fixture is valid JSON");
    assert!(
        value.get("spec").is_none(),
        "fixture must be spec-less to exercise the regression"
    );

    // Deserialize via serde_json (the exact path kube's Api decode takes).
    let exporter: Exporter = serde_json::from_str(&raw).expect("spec-less Exporter must decode");

    // Spec defaults to the Go zero value.
    assert_eq!(exporter.spec, ExporterSpec::default());
    // Metadata and status survived.
    assert_eq!(exporter.metadata.name.as_deref(), Some("power-exporter"));
    assert_eq!(exporter.metadata.namespace.as_deref(), Some("jumpstarter-lab"));
    let status = exporter.status.as_ref().expect("status present");
    assert_eq!(status.exporter_status, Some(ExporterStatusValue::Offline));
    assert_eq!(status.endpoint.as_deref(), Some("127.0.0.1:8082"));

    // Re-serialization reaches a serde fixed point (spec is written back as an
    // empty object rather than Go's `omitempty` absence — the derived, macro
    // -written Serialize always emits `spec`; read tolerance is the contract).
    // The Exporter wrapper does not derive PartialEq, so compare via JSON.
    let reserialized = serde_json::to_value(&exporter).expect("re-serialize Exporter");
    assert_eq!(reserialized["spec"], json!({}));
    let reparsed: Exporter =
        serde_json::from_value(reserialized.clone()).expect("re-deserialize Exporter");
    assert_eq!(
        serde_json::to_value(&reparsed).expect("re-serialize reparsed Exporter"),
        reserialized
    );
}

/// A spec-less Client (Go `Client.Spec ClientSpec json:"spec,omitempty"`).
#[test]
fn client_deserializes_spec_less() {
    let spec_less = json!({
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Client",
        "metadata": { "name": "my-client", "namespace": "default" },
        "status": { "endpoint": "grpc.example.com:8082" },
    });

    let client: Client = serde_json::from_value(spec_less).expect("spec-less Client must decode");
    assert_eq!(client.spec, ClientSpec::default());
    assert_eq!(client.metadata.name.as_deref(), Some("my-client"));
    assert_eq!(
        client.status.as_ref().map(|s| s.endpoint.as_str()),
        Some("grpc.example.com:8082")
    );

    let reserialized = serde_json::to_value(&client).expect("re-serialize Client");
    assert_eq!(reserialized["spec"], json!({}));
    let reparsed: Client = serde_json::from_value(reserialized).expect("re-deserialize Client");
    assert_eq!(reparsed, client);
}

/// A spec-less ExporterAccessPolicy (Go
/// `ExporterAccessPolicy.Spec ...Spec json:"spec,omitempty"`).
#[test]
fn exporter_access_policy_deserializes_spec_less() {
    let spec_less = json!({
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "ExporterAccessPolicy",
        "metadata": { "name": "default", "namespace": "jumpstarter-lab" },
    });

    let policy: ExporterAccessPolicy =
        serde_json::from_value(spec_less).expect("spec-less ExporterAccessPolicy must decode");
    assert_eq!(policy.spec, ExporterAccessPolicySpec::default());
    assert_eq!(policy.metadata.name.as_deref(), Some("default"));
    assert!(policy.spec.policies.is_empty());

    // The exporterSelector struct is always serialized (Go never treats a
    // struct as empty), so the default spec re-serializes as
    // `{"exporterSelector":{}}` rather than absent.
    let reserialized = serde_json::to_value(&policy).expect("re-serialize policy");
    assert_eq!(reserialized["spec"], json!({ "exporterSelector": {} }));
    let reparsed: ExporterAccessPolicy =
        serde_json::from_value(reserialized).expect("re-deserialize policy");
    assert_eq!(reparsed, policy);
}
