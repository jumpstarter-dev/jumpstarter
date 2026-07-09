//! The `Device` type embedded in the Exporter status, ported from
//! `controller/api/v1alpha1/device_types.go`.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Device represents a driver instance reported by an exporter.
///
/// Field names mirror the Go json tags exactly; note that `parent_uuid` is
/// snake_case on the wire (not camelCase), so no `rename_all` is applied.
///
/// The Go `uuid` field is a non-pointer `string` with `omitempty`: Go cannot
/// distinguish empty from absent and drops empty strings on marshal. It is
/// modeled as `Option<String>` here so the generated schema matches the
/// controller-gen output (plain `type: string`, no default, not required).
// go: device_types.go:4 (Device)
#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, JsonSchema)]
pub struct Device {
    /// Uuid is the unique identifier of the device within the exporter.
    // go: device_types.go:6 `json:"uuid,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uuid: Option<String>,
    /// ParentUuid is the UUID of the parent device, if this is a child device.
    // go: device_types.go:8 `json:"parent_uuid,omitempty"` (snake_case wire name)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_uuid: Option<String>,
    /// Labels are key-value pairs associated with the device.
    // go: device_types.go:10 `json:"labels,omitempty"`
    #[serde(skip_serializing_if = "Option::is_none")]
    pub labels: Option<BTreeMap<String, String>>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn empty_device_serializes_to_empty_object() {
        let device = Device::default();
        assert_eq!(serde_json::to_value(&device).unwrap(), json!({}));
    }

    #[test]
    fn wire_names_match_go_json_tags() {
        let device = Device {
            uuid: Some("aaaa".into()),
            parent_uuid: Some("bbbb".into()),
            labels: Some(BTreeMap::from([("board".to_string(), "rpi4".to_string())])),
        };
        // `parent_uuid` must stay snake_case — it is snake_case in the Go json tag.
        assert_eq!(
            serde_json::to_value(&device).unwrap(),
            json!({
                "uuid": "aaaa",
                "parent_uuid": "bbbb",
                "labels": {"board": "rpi4"},
            })
        );
    }

    #[test]
    fn missing_fields_deserialize_to_none() {
        let device: Device = serde_json::from_value(json!({"uuid": "aaaa"})).unwrap();
        assert_eq!(device.uuid.as_deref(), Some("aaaa"));
        assert_eq!(device.parent_uuid, None);
        assert_eq!(device.labels, None);
    }
}
