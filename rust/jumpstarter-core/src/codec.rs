//! JSON ⇄ `google.protobuf.Value` codec wrappers used at the foreign-host boundary.
//!
//! The foreign host (Python now; Kotlin/C later) exchanges driver-call args/results as
//! **plain JSON strings**; Rust owns the canonical proto-`Value` quirks via
//! [`jumpstarter_protocol::value`] (int→f64, tuple→list, non-finite→null, …). This is
//! the "Rust owns the codec, the foreign side crosses JSON" decision — authored once
//! here so every binding behaves identically.

use jumpstarter_protocol::value;
use prost_types::Value;
use serde_json::Value as Json;

use crate::error::CodecError;

/// Host side (Rust calls the host): decode proto `Value` args into one JSON array
/// string the foreign host deserializes back into positional args.
pub fn args_to_json(args: &[Value]) -> Result<String, CodecError> {
    let decoded: Vec<Json> = value::decode_args(args);
    Ok(serde_json::to_string(&Json::Array(decoded))?)
}

/// Host side: encode the foreign host's single JSON result string into a proto `Value`.
pub fn json_result_to_value(result_json: &str) -> Result<Value, CodecError> {
    let json: Json = serde_json::from_str(result_json)?;
    Ok(value::encode_value(&json))
}

/// Client side (foreign client calls Rust): encode a foreign-supplied JSON array string
/// of args into proto `Value`s for the wire `DriverCallRequest`.
pub fn json_args_to_values(args_json: &str) -> Result<Vec<Value>, CodecError> {
    match serde_json::from_str::<Json>(args_json)? {
        Json::Array(items) => Ok(value::encode_args(&items)),
        // A non-array crossing is treated as a single positional arg (defensive).
        other => Ok(vec![value::encode_value(&other)]),
    }
}

/// Client side: decode a wire `DriverCallResponse.result` proto `Value` into a JSON
/// string for the foreign client.
pub fn value_result_to_json(result: &Value) -> Result<String, CodecError> {
    Ok(serde_json::to_string(&value::decode_value(result))?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn args_round_trip_host_to_client() {
        // proto args -> json (host) ; json -> proto args (client) must agree on values.
        let original = value::encode_args(&[json!("on"), json!(42), json!([1, 2]), json!({"k": "v"})]);
        let as_json = args_to_json(&original).unwrap();
        let back = json_args_to_values(&as_json).unwrap();
        // Compare via the JSON projection (the wire collapses ints->f64 identically both ways).
        assert_eq!(value::decode_args(&back), value::decode_args(&original));
    }

    #[test]
    fn result_round_trip() {
        let v = value::encode_value(&json!({"voltage": 3.3, "ok": true, "ports": [22, 80]}));
        let s = value_result_to_json(&v).unwrap();
        let back = json_result_to_value(&s).unwrap();
        assert_eq!(value::decode_value(&back), value::decode_value(&v));
    }

    #[test]
    fn integer_collapses_to_float_like_python() {
        let v = json_result_to_value("42").unwrap();
        // wire has only doubles; 42 -> 42.0
        assert_eq!(value::decode_value(&v), json!(42.0));
    }

    #[test]
    fn malformed_json_is_a_codec_error() {
        assert!(json_result_to_value("{not json").is_err());
    }
}
