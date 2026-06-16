//! The `google.protobuf.Value` argument/result codec.
//!
//! Driver-call arguments and results travel as `google.protobuf.Value`
//! (`DriverCallRequest.args`, `DriverCallResponse.result` —
//! `protocol/proto/jumpstarter/v1/jumpstarter.proto:171,177`). This module
//! replicates, bit-for-bit, the Python `encode_value`/`decode_value` semantics
//! (`python/packages/jumpstarter/jumpstarter/common/serde.py:6-14`), which are the
//! de-facto wire contract (spec `09-rust-core-requirements.md` §2.4).
//!
//! The Python codec is `json_format.ParseDict(TypeAdapter(Any).dump_python(v,
//! mode="json"), Value())` and its inverse. The intermediate is always a
//! JSON-compatible value, so the natural Rust-native representation is
//! [`serde_json::Value`], and the codec is a bidirectional map between it and
//! [`prost_types::Value`].
//!
//! ## Quirks that are part of the contract (spec §2.4)
//!
//! - **integers become doubles.** `google.protobuf.Value` only has
//!   `number_value` (an IEEE-754 double), so every number — including integers —
//!   is encoded as `f64`. A non-Rust/Python peer therefore observes floats, and
//!   an `i64` round-tripped through a `Value` comes back as a float. This is
//!   intentional and asserted in the tests.
//! - **tuples become lists.** Python tuples serialize through JSON mode as arrays;
//!   in the Rust value model sequences are already [`serde_json::Value::Array`],
//!   so this falls out for free.
//! - **bytes must be UTF-8.** Python's JSON-mode dump encodes `bytes` by decoding
//!   them as UTF-8 into a string (and errors otherwise). The Rust value model has
//!   no bytes variant — a caller passes a [`String`] — so the UTF-8 requirement is
//!   structural here.
//!
//! ## Non-finite numbers
//!
//! `f64` NaN/±Infinity are not representable in JSON and do not occur in
//! well-formed driver data; on decode they are mapped to
//! [`serde_json::Value::Null`] rather than panicking. (Python's path produces
//! non-standard `NaN`/`Infinity` JSON tokens, which are equally unusable by
//! interop peers.)

use prost_types::value::Kind;
use prost_types::{ListValue, Struct, Value};
use serde_json::Value as Json;

/// Encode a JSON-shaped value into a `google.protobuf.Value`.
///
/// Mirrors Python `encode_value` (`common/serde.py:9`).
pub fn encode_value(v: &Json) -> Value {
    let kind = match v {
        Json::Null => Kind::NullValue(0), // google.protobuf.NULL_VALUE = 0
        Json::Bool(b) => Kind::BoolValue(*b),
        // All numbers collapse to a double — the int->float quirk (§2.4).
        Json::Number(n) => Kind::NumberValue(n.as_f64().unwrap_or(f64::NAN)),
        Json::String(s) => Kind::StringValue(s.clone()),
        Json::Array(items) => Kind::ListValue(ListValue {
            values: items.iter().map(encode_value).collect(),
        }),
        Json::Object(fields) => Kind::StructValue(Struct {
            fields: fields
                .iter()
                .map(|(k, v)| (k.clone(), encode_value(v)))
                .collect(),
        }),
    };
    Value { kind: Some(kind) }
}

/// Decode a `google.protobuf.Value` into a JSON-shaped value.
///
/// Mirrors Python `decode_value` (`common/serde.py:13`). Numbers always decode to
/// `f64` (the wire type), so integers do not survive a round-trip as integers —
/// see the module-level quirk note.
pub fn decode_value(v: &Value) -> Json {
    match &v.kind {
        // An empty/absent `Value` is treated as null, matching how an unset oneof
        // decodes (Python `MessageToDict` of an empty Value yields null).
        None => Json::Null,
        Some(Kind::NullValue(_)) => Json::Null,
        Some(Kind::BoolValue(b)) => Json::Bool(*b),
        Some(Kind::NumberValue(n)) => number_to_json(*n),
        Some(Kind::StringValue(s)) => Json::String(s.clone()),
        Some(Kind::ListValue(l)) => Json::Array(l.values.iter().map(decode_value).collect()),
        Some(Kind::StructValue(s)) => Json::Object(
            s.fields
                .iter()
                .map(|(k, v)| (k.clone(), decode_value(v)))
                .collect(),
        ),
    }
}

/// Encode a positional argument list (`DriverCallRequest.args`).
pub fn encode_args(args: &[Json]) -> Vec<Value> {
    args.iter().map(encode_value).collect()
}

/// Decode a positional argument list back into JSON-shaped values.
pub fn decode_args(args: &[Value]) -> Vec<Json> {
    args.iter().map(decode_value).collect()
}

/// Map an `f64` to a JSON number, falling back to null for non-finite values
/// (NaN/±Inf are not JSON-representable).
fn number_to_json(n: f64) -> Json {
    serde_json::Number::from_f64(n)
        .map(Json::Number)
        .unwrap_or(Json::Null)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Compare two JSON values treating all numbers as `f64` — the decode side
    /// always produces float numbers (the wire has only doubles), so `42` and
    /// `42.0` must be considered equal when checking round-trips.
    fn json_eq_f64(a: &Json, b: &Json) -> bool {
        match (a, b) {
            (Json::Number(x), Json::Number(y)) => match (x.as_f64(), y.as_f64()) {
                (Some(x), Some(y)) => x == y,
                _ => false,
            },
            (Json::Array(x), Json::Array(y)) => {
                x.len() == y.len() && x.iter().zip(y).all(|(a, b)| json_eq_f64(a, b))
            }
            (Json::Object(x), Json::Object(y)) => {
                x.len() == y.len()
                    && x.iter()
                        .all(|(k, v)| y.get(k).is_some_and(|w| json_eq_f64(v, w)))
            }
            _ => a == b,
        }
    }

    fn kind(v: &Value) -> &Kind {
        v.kind.as_ref().unwrap()
    }

    #[test]
    fn encodes_each_scalar_variant() {
        assert!(matches!(
            kind(&encode_value(&Json::Null)),
            Kind::NullValue(0)
        ));
        assert!(matches!(
            kind(&encode_value(&json!(true))),
            Kind::BoolValue(true)
        ));
        assert!(matches!(kind(&encode_value(&json!("hi"))), Kind::StringValue(s) if s == "hi"));
        assert!(matches!(kind(&encode_value(&json!(3.5))), Kind::NumberValue(n) if *n == 3.5));
    }

    #[test]
    fn integers_encode_as_doubles() {
        // The int->float quirk: an integer JSON number must become number_value.
        match kind(&encode_value(&json!(42))) {
            Kind::NumberValue(n) => assert_eq!(*n, 42.0),
            other => panic!("expected NumberValue, got {other:?}"),
        }
        // And it does not survive a round-trip as an integer.
        let decoded = decode_value(&encode_value(&json!(42)));
        assert!(decoded.is_f64(), "integer must decode back as a float");
        assert_eq!(decoded.as_f64(), Some(42.0));
        assert_ne!(decoded, json!(42)); // 42 (i64) != 42.0 (f64) under serde_json
    }

    #[test]
    fn negative_and_large_integers() {
        assert_eq!(decode_value(&encode_value(&json!(-7))).as_f64(), Some(-7.0));
        // 2^53 + 1 is the f64 integer-precision boundary; both Python and Rust
        // round identically, so this is the contract, not a bug.
        let big = json!(9007199254740993i64);
        assert_eq!(
            decode_value(&encode_value(&big)).as_f64(),
            Some(9007199254740992.0)
        );
    }

    #[test]
    fn nested_list_and_struct_roundtrip() {
        let v = json!({
            "name": "device",
            "ports": [22, 80, 443],
            "meta": {"enabled": true, "ratio": 0.75},
            "tags": ["a", "b"],
            "nothing": null,
        });
        let round = decode_value(&encode_value(&v));
        assert!(
            json_eq_f64(&round, &v),
            "round-trip mismatch:\n{round:#}\nvs\n{v:#}"
        );
    }

    #[test]
    fn empty_containers_and_string() {
        for v in [json!([]), json!({}), json!("")] {
            let round = decode_value(&encode_value(&v));
            assert_eq!(round, v);
        }
    }

    #[test]
    fn unicode_strings_and_keys() {
        let v = json!({"café": "héllo 日本語", "ключ": ["🦀", ""]});
        let round = decode_value(&encode_value(&v));
        assert!(json_eq_f64(&round, &v));
    }

    #[test]
    fn absent_kind_decodes_to_null() {
        assert_eq!(decode_value(&Value { kind: None }), Json::Null);
    }

    #[test]
    fn non_finite_numbers_decode_to_null() {
        let nan = Value {
            kind: Some(Kind::NumberValue(f64::NAN)),
        };
        let inf = Value {
            kind: Some(Kind::NumberValue(f64::INFINITY)),
        };
        assert_eq!(decode_value(&nan), Json::Null);
        assert_eq!(decode_value(&inf), Json::Null);
    }

    #[test]
    fn args_helpers_roundtrip() {
        let args = vec![json!("on"), json!(42), json!([1, 2]), json!({"k": "v"})];
        let decoded = decode_args(&encode_args(&args));
        assert_eq!(decoded.len(), args.len());
        for (d, a) in decoded.iter().zip(&args) {
            assert!(json_eq_f64(d, a));
        }
    }
}
