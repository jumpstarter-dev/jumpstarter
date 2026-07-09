//! Serde round-trip tests against the sample CRs shipped in the retained Go
//! tree (`controller/config/samples/`), read in place so sample drift over
//! there trips this suite.
//!
//! For every sample document of one of the four `jumpstarter.dev/v1alpha1`
//! kinds: YAML → typed struct → JSON must equal the input parsed straight to
//! `serde_json::Value`, modulo Go's canonical re-marshaling of duration
//! strings (Go itself re-marshals the sample's `24h` as `24h0m0s`, so
//! byte-identity with the input is not even Go behavior — the input side is
//! canonicalized symmetrically before comparing). Deserialization must also
//! tolerate unknown fields (K8s pruning semantics: no `deny_unknown_fields`).

use std::str::FromStr;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use jumpstarter_controller_api::access_policy::ExporterAccessPolicy;
use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::exporter::Exporter;
use jumpstarter_controller_api::go_duration::GoDuration;
use jumpstarter_controller_api::lease::Lease;

const API_VERSION: &str = "jumpstarter.dev/v1alpha1";

/// Globs `controller/config/samples/` (in place) and returns every YAML
/// document whose kind is one of the four `jumpstarter.dev/v1alpha1` kinds,
/// as `(source file name, kind, document)`.
fn sample_documents() -> Vec<(String, String, Value)> {
    let dir = format!(
        "{}/../../controller/config/samples",
        env!("CARGO_MANIFEST_DIR"),
    );
    let mut documents = Vec::new();
    let mut entries: Vec<_> = std::fs::read_dir(&dir)
        .unwrap_or_else(|err| panic!("read samples dir {dir}: {err}"))
        .map(|entry| entry.expect("read dir entry").path())
        .filter(|path| {
            matches!(
                path.extension().and_then(|e| e.to_str()),
                Some("yaml" | "yml")
            )
        })
        .collect();
    entries.sort();

    for path in entries {
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|err| panic!("read sample {}: {err}", path.display()));
        // Samples may be multi-document YAML.
        for document in serde_yaml_ng::Deserializer::from_str(&text) {
            let value = match Value::deserialize(document) {
                Ok(value) => value,
                // Non-CR yaml in the directory (e.g. kustomization.yaml
                // fragments) that still parses is filtered below; skip
                // anything that is not a mapping at all.
                Err(err) => panic!("parse sample {}: {err}", path.display()),
            };
            if value.get("apiVersion").and_then(Value::as_str) != Some(API_VERSION) {
                continue;
            }
            let Some(kind) = value.get("kind").and_then(Value::as_str) else {
                continue;
            };
            if matches!(
                kind,
                "Exporter" | "Client" | "Lease" | "ExporterAccessPolicy"
            ) {
                let file = path.file_name().unwrap().to_string_lossy().into_owned();
                documents.push((file, kind.to_string(), value));
            }
        }
    }
    documents
}

/// Canonicalizes Go-duration strings in place, at the keys the four CRDs use
/// for durations (`spec.duration`, `policies[].maximumDuration`). Applied to
/// the INPUT side only, mirroring what Go's own marshal round-trip does to
/// human-form durations ("24h" → "24h0m0s").
fn canonicalize_durations(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for (key, child) in map.iter_mut() {
                if matches!(key.as_str(), "duration" | "maximumDuration") {
                    if let Value::String(text) = child {
                        if let Ok(duration) = GoDuration::from_str(text) {
                            *child = Value::String(duration.to_string());
                        }
                    }
                } else {
                    canonicalize_durations(child);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                canonicalize_durations(item);
            }
        }
        _ => {}
    }
}

/// YAML → typed struct → JSON == canonicalized input, plus unknown-field
/// tolerance for the same document.
fn assert_round_trip<T: DeserializeOwned + Serialize>(file: &str, kind: &str, input: &Value) {
    let typed: T = serde_json::from_value(input.clone())
        .unwrap_or_else(|err| panic!("{file}: {kind} sample must deserialize: {err}"));
    let output = serde_json::to_value(&typed)
        .unwrap_or_else(|err| panic!("{file}: {kind} sample must serialize: {err}"));

    let mut expected = input.clone();
    canonicalize_durations(&mut expected);
    assert_eq!(
        output, expected,
        "{file}: {kind} round trip diverged from the (duration-canonicalized) input",
    );

    // Unknown fields must be tolerated at every level kube-apiserver would
    // prune them (no deny_unknown_fields anywhere).
    let mut with_unknown = input.clone();
    with_unknown["unknownTopLevelField"] = serde_json::json!("ignored");
    with_unknown["spec"]["unknownSpecField"] = serde_json::json!({"nested": true});
    with_unknown["metadata"]["unknownMetaField"] = serde_json::json!(42);
    if let Err(err) = serde_json::from_value::<T>(with_unknown) {
        panic!("{file}: {kind} must tolerate unknown fields, got: {err}");
    }
}

#[test]
fn samples_round_trip() {
    let documents = sample_documents();

    // The suite is only meaningful if the Go tree still ships a sample for
    // every kind; fail loudly when samples move or get renamed.
    for expected in ["Exporter", "Client", "Lease", "ExporterAccessPolicy"] {
        assert!(
            documents.iter().any(|(_, kind, _)| kind == expected),
            "no {expected} sample found in controller/config/samples \
             (found: {:?})",
            documents
                .iter()
                .map(|(file, kind, _)| format!("{file}:{kind}"))
                .collect::<Vec<_>>(),
        );
    }

    for (file, kind, document) in &documents {
        match kind.as_str() {
            "Exporter" => assert_round_trip::<Exporter>(file, kind, document),
            "Client" => assert_round_trip::<Client>(file, kind, document),
            "Lease" => assert_round_trip::<Lease>(file, kind, document),
            "ExporterAccessPolicy" => {
                assert_round_trip::<ExporterAccessPolicy>(file, kind, document)
            }
            other => unreachable!("unexpected kind {other}"),
        }
    }
}
