//! CRD parity harness: structurally diffs the CRDs generated from the Rust
//! types (`kube::CustomResourceExt::crd()`) against the controller-gen golden
//! YAML in `controller/deploy/operator/config/crd/bases/`, read **in place**
//! from the retained Go tree so any drift over there trips this suite.
//!
//! Normalization (applied to BOTH sides):
//! - every schema-annotation `description` is stripped (descriptions are
//!   structural-only by decision; a *property named* `description`, as in the
//!   ExporterAccessPolicy `Policy` type, is preserved)
//! - `metadata.annotations["controller-gen.kubebuilder.io/version"]` is
//!   dropped (tooling provenance, not schema)
//! - `metadata.creationTimestamp: null` is dropped (older controller-gen
//!   emitted it; k8s-openapi omits it)
//! - the top-level `status` stanza is dropped (server-populated)
//!
//! The `overlay()` hook patches the RUST side only, as an explicit escape
//! hatch for divergences kube-derive/schemars cannot express. Target: empty.
//! Every entry must carry a comment explaining the exact gap.

use k8s_openapi::apiextensions_apiserver::pkg::apis::apiextensions::v1::CustomResourceDefinition;
use kube::CustomResourceExt;
use serde_json::Value;

use jumpstarter_controller_api::access_policy::ExporterAccessPolicy;
use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::exporter::Exporter;
use jumpstarter_controller_api::lease::Lease;

/// Loads a golden CRD from the retained Go tree, in place.
fn golden(file: &str) -> Value {
    let path = format!(
        "{}/../../controller/deploy/operator/config/crd/bases/{file}",
        env!("CARGO_MANIFEST_DIR"),
    );
    let yaml = std::fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("read golden CRD {path}: {err}"));
    serde_yaml_ng::from_str(&yaml).unwrap_or_else(|err| panic!("parse golden CRD {path}: {err}"))
}

/// Recursively strips schema-annotation `description` keys.
///
/// `parent_is_properties` distinguishes a schema object (where `description`
/// is an annotation to strip) from a `properties` map (where `description`
/// may be a real property name — e.g. `Policy.description` in the
/// ExporterAccessPolicy CRD — and its schema *value* still gets recursed).
fn strip_descriptions(value: &mut Value, parent_is_properties: bool) {
    match value {
        Value::Object(map) => {
            if !parent_is_properties {
                map.remove("description");
            }
            for (key, child) in map.iter_mut() {
                strip_descriptions(child, !parent_is_properties && key == "properties");
            }
        }
        Value::Array(items) => {
            for item in items {
                strip_descriptions(item, false);
            }
        }
        _ => {}
    }
}

/// Applies the shared normalization to one CRD document (either side).
fn normalize(crd: &mut Value) {
    strip_descriptions(crd, false);

    if let Some(metadata) = crd.get_mut("metadata").and_then(Value::as_object_mut) {
        if let Some(annotations) = metadata
            .get_mut("annotations")
            .and_then(Value::as_object_mut)
        {
            annotations.remove("controller-gen.kubebuilder.io/version");
            if annotations.is_empty() {
                metadata.remove("annotations");
            }
        }
        if metadata.get("creationTimestamp") == Some(&Value::Null) {
            metadata.remove("creationTimestamp");
        }
    }

    if let Some(crd) = crd.as_object_mut() {
        crd.remove("status");
    }
}

/// Explicit patch list applied to the RUST side only, for divergences
/// kube-derive/schemars cannot express. Target: empty — every entry must
/// carry a comment explaining the gap and the JSON pointer it touches.
///
/// All four entries are systematic kube-derive 4.0 CRD-assembly residue
/// (verified against `kube-derive-4.0.0/src/custom_resource.rs`), not
/// per-type schema gaps: the derive macro exposes no attribute to change any
/// of them, and they apply identically to every kind.
fn overlay(rust: &mut Value, kind: &str) {
    // (1) /spec/names: kube-derive never emits `listKind` and always emits
    // `categories`/`shortNames` (as [] when unset), while controller-gen
    // derives `listKind: <Kind>List` and omits empty name lists.
    let names = &mut rust["spec"]["names"];
    names["listKind"] = Value::String(format!("{kind}List"));
    for list in ["categories", "shortNames"] {
        if names[list] == Value::Array(vec![]) {
            names.as_object_mut().unwrap().remove(list);
        }
    }

    // (2) /spec/versions/0/additionalPrinterColumns: kube-derive always
    // emits the array (as [] without printcolumns); controller-gen omits it.
    let version = &mut rust["spec"]["versions"][0];
    if version["additionalPrinterColumns"] == Value::Array(vec![]) {
        version
            .as_object_mut()
            .unwrap()
            .remove("additionalPrinterColumns");
    }

    // (3) Root schema framing: kube-derive's generated root type yields a
    // schemars `title` and `required: ["spec"]`, and omits the
    // `apiVersion`/`kind`/`metadata` properties; controller-gen emits no
    // title, never requires `spec`, and documents the three meta properties.
    // (Both shapes validate identically: the apiserver handles the meta
    // fields itself, and CRs without .spec are rejected by the CEL/defaulting
    // layer, not schema-required.)
    let root = version["schema"]["openAPIV3Schema"]
        .as_object_mut()
        .unwrap();
    root.remove("title");
    root.remove("required");
    let properties = root["properties"].as_object_mut().unwrap();
    properties.insert("apiVersion".into(), serde_json::json!({ "type": "string" }));
    properties.insert("kind".into(), serde_json::json!({ "type": "string" }));
    properties.insert("metadata".into(), serde_json::json!({ "type": "object" }));

    // (4) `nullable: true` everywhere: kube-derive hardwires schemars'
    // `AddNullable` transform, marking every `Option<T>` field nullable;
    // controller-gen never emits `nullable` for `omitempty` pointer fields.
    // (Rust-generated CRDs would additionally accept explicit `null` values
    // where the Go CRDs reject them — a strict superset, and moot while the
    // deployed CRDs remain the Go YAML.)
    strip_nullable(&mut rust["spec"]["versions"][0]["schema"]["openAPIV3Schema"]);
}

/// Recursively removes `nullable: true` (overlay entry 4).
fn strip_nullable(value: &mut Value) {
    match value {
        Value::Object(map) => {
            if map.get("nullable") == Some(&Value::Bool(true)) {
                map.remove("nullable");
            }
            for child in map.values_mut() {
                strip_nullable(child);
            }
        }
        Value::Array(items) => {
            for item in items {
                strip_nullable(item);
            }
        }
        _ => {}
    }
}

/// Deep-compares two JSON values, recording a JSON-pointer path for every
/// divergence (missing key, extra key, type mismatch, scalar mismatch,
/// array-length mismatch).
fn diff(golden: &Value, rust: &Value, path: &str, out: &mut Vec<String>) {
    match (golden, rust) {
        (Value::Object(g), Value::Object(r)) => {
            for (key, gv) in g {
                let child = format!("{path}/{}", key.replace('~', "~0").replace('/', "~1"));
                match r.get(key) {
                    Some(rv) => diff(gv, rv, &child, out),
                    None => out.push(format!("{child}: missing on rust side (golden: {gv})")),
                }
            }
            for key in r.keys() {
                if !g.contains_key(key) {
                    let child = format!("{path}/{}", key.replace('~', "~0").replace('/', "~1"));
                    out.push(format!("{child}: extra on rust side (rust: {})", r[key]));
                }
            }
        }
        (Value::Array(g), Value::Array(r)) => {
            if g.len() != r.len() {
                out.push(format!(
                    "{path}: array length mismatch (golden {} vs rust {})",
                    g.len(),
                    r.len()
                ));
            }
            for (index, (gv, rv)) in g.iter().zip(r.iter()).enumerate() {
                diff(gv, rv, &format!("{path}/{index}"), out);
            }
        }
        // Compare numbers numerically: kube round-trips schemas through typed
        // `JSONSchemaProps` where e.g. `minimum` is f64, so a golden `0`
        // serializes as `0.0` on the Rust side.
        (Value::Number(g), Value::Number(r)) => {
            if g.as_f64() != r.as_f64() {
                out.push(format!("{path}: golden {g} != rust {r}"));
            }
        }
        (g, r) => {
            if g != r {
                out.push(format!("{path}: golden {g} != rust {r}"));
            }
        }
    }
}

/// Runs the full parity check for one kind.
fn assert_parity(kind: &str, golden_file: &str, crd: CustomResourceDefinition) {
    let mut golden_value = golden(golden_file);
    let mut rust_value = serde_json::to_value(&crd)
        .unwrap_or_else(|err| panic!("serialize generated {kind} CRD: {err}"));

    normalize(&mut golden_value);
    normalize(&mut rust_value);
    overlay(&mut rust_value, kind);

    let mut divergences = Vec::new();
    diff(&golden_value, &rust_value, "", &mut divergences);

    assert!(
        divergences.is_empty(),
        "{kind} CRD diverges from golden {golden_file} at {} path(s):\n  {}",
        divergences.len(),
        divergences.join("\n  "),
    );
}

#[test]
fn exporter_crd_parity() {
    assert_parity(
        "Exporter",
        "jumpstarter.dev_exporters.yaml",
        Exporter::crd(),
    );
}

#[test]
fn client_crd_parity() {
    assert_parity("Client", "jumpstarter.dev_clients.yaml", Client::crd());
}

#[test]
fn lease_crd_parity() {
    assert_parity("Lease", "jumpstarter.dev_leases.yaml", Lease::crd());
}

#[test]
fn exporter_access_policy_crd_parity() {
    assert_parity(
        "ExporterAccessPolicy",
        "jumpstarter.dev_exporteraccesspolicies.yaml",
        ExporterAccessPolicy::crd(),
    );
}
