//! The router endpoint map, ported from `controller/internal/config/types.go`
//! (`Router` / `RouterEntry` — the ConfigMap's "router" key).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::serde_util::null_default;

/// Router represents the router configuration mapping.
/// This is a map where keys are router names (e.g., "default", "router-1", "router-2")
/// and values are RouterEntry structs containing endpoint and label information.
/// This matches the YAML structure in the ConfigMap's "router" key.
///
/// A `BTreeMap` mirrors Go's serialization order: sigs.k8s.io/yaml emits map
/// keys alphabetically.
pub type Router = BTreeMap<String, RouterEntry>;

/// RouterEntry defines a single router endpoint configuration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RouterEntry {
    /// Endpoint is the router's gRPC endpoint address (e.g., "router-0.example.com:443")
    // Go: `json:"endpoint"` (no omitempty) — always serialized.
    #[serde(default)]
    pub endpoint: String,

    /// Labels are optional labels to associate with this router entry.
    /// Used to distinguish between different router instances.
    // Go: `json:"labels,omitempty"` map — nil and empty are both omitted;
    // an explicit `labels: null` decodes to empty, like Go.
    #[serde(
        default,
        deserialize_with = "null_default",
        skip_serializing_if = "BTreeMap::is_empty"
    )]
    pub labels: BTreeMap<String, String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    /// Ported from Go `TestRouterRoundTrip` (types_test.go).
    #[test]
    fn router_round_trip() {
        let mut original = Router::new();
        original.insert(
            "default".into(),
            RouterEntry {
                endpoint: "router-0.example.com:443".into(),
                labels: BTreeMap::new(),
            },
        );
        original.insert(
            "router-1".into(),
            RouterEntry {
                endpoint: "router-1.example.com:443".into(),
                labels: labels(&[("router-index", "1")]),
            },
        );
        original.insert(
            "router-2".into(),
            RouterEntry {
                endpoint: "router-2.example.com:443".into(),
                labels: labels(&[("router-index", "2"), ("zone", "us-east")]),
            },
        );

        let yaml = serde_yaml_ng::to_string(&original).expect("marshal router");
        let parsed: Router = serde_yaml_ng::from_str(&yaml).expect("unmarshal router");

        assert_eq!(parsed.len(), original.len());
        assert_eq!(parsed["default"].endpoint, original["default"].endpoint);
        assert_eq!(parsed["router-1"].endpoint, original["router-1"].endpoint);
        assert_eq!(parsed["router-1"].labels["router-index"], "1");
        assert_eq!(parsed["router-2"].labels.len(), 2);
        assert_eq!(parsed, original);
    }

    /// Ported from Go `TestParseYAMLToRouter` (types_test.go).
    #[test]
    fn parse_yaml_to_router() {
        let yaml_input = r#"
default:
  endpoint: router.example.com:443
router-1:
  endpoint: router-1.example.com:443
  labels:
    router-index: "1"
router-2:
  endpoint: router-2.example.com:443
  labels:
    router-index: "2"
"#;
        let router: Router = serde_yaml_ng::from_str(yaml_input).expect("unmarshal yaml");

        assert_eq!(router.len(), 3);
        assert!(router["default"].labels.is_empty());
        assert!(!router["router-1"].labels.is_empty());
    }

    /// Empty labels are omitted on serialize; explicit null decodes to empty.
    #[test]
    fn labels_empty_and_null() {
        let entry = RouterEntry {
            endpoint: "router-0.example.com:443".into(),
            labels: BTreeMap::new(),
        };
        let yaml = serde_yaml_ng::to_string(&entry).expect("marshal");
        assert!(!yaml.contains("labels"), "yaml was:\n{yaml}");

        let parsed: RouterEntry =
            serde_yaml_ng::from_str("endpoint: router-0.example.com:443\nlabels: null\n")
                .expect("null labels");
        assert!(parsed.labels.is_empty());
    }
}
