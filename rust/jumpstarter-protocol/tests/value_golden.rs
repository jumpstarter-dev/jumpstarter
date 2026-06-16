//! Differential golden tests for the `google.protobuf.Value` codec.
//!
//! The fixtures in `fixtures/value_golden.json` are produced by the **real Python
//! codec** (`jumpstarter.common.serde.encode_value`) via
//! `fixtures/generate_value_golden.py`. Each fixture carries:
//!   - `rust_input`  — the JSON-normalized input both codecs consume
//!   - `value_b64`   — base64 of the `Value` wire bytes Python produced
//!   - `decoded`     — Python's `MessageToDict` of that `Value`
//!
//! These tests assert the Rust codec is wire-compatible with Python in both
//! directions. Regenerate the fixtures (and re-review the diff) whenever the
//! Python codec changes:
//!
//! ```sh
//! python/.venv/bin/python \
//!   rust/jumpstarter-protocol/tests/fixtures/generate_value_golden.py
//! ```

use base64::Engine as _;
use jumpstarter_protocol::{decode_value, encode_value};
use prost::Message;
use prost_types::value::Kind;
use prost_types::Value;
use serde_json::Value as Json;

const GOLDEN: &str = include_str!("fixtures/value_golden.json");

/// Does this `Value` contain a `Struct` anywhere? Protobuf map serialization
/// order is not guaranteed across implementations, so byte-equality is only a
/// valid check for struct-free values; struct-bearing values are checked
/// semantically (via prost's order-insensitive `PartialEq`).
fn contains_struct(v: &Value) -> bool {
    match &v.kind {
        Some(Kind::StructValue(_)) => true,
        Some(Kind::ListValue(l)) => l.values.iter().any(contains_struct),
        _ => false,
    }
}

/// JSON equality treating all numbers as `f64` — the wire has only doubles, so
/// the decode side always yields floats (`42` vs `42.0` must compare equal).
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

struct Fixture {
    name: String,
    rust_input: Json,
    golden_bytes: Vec<u8>,
    golden: Value,
    decoded: Json,
}

fn load_fixtures() -> Vec<Fixture> {
    let raw: Vec<serde_json::Map<String, Json>> =
        serde_json::from_str(GOLDEN).expect("parse value_golden.json");
    assert!(!raw.is_empty(), "no golden fixtures loaded");
    raw.into_iter()
        .map(|m| {
            let name = m["name"].as_str().unwrap().to_string();
            let b64 = m["value_b64"].as_str().unwrap();
            let golden_bytes = base64::engine::general_purpose::STANDARD
                .decode(b64)
                .unwrap_or_else(|e| panic!("{name}: bad base64: {e}"));
            let golden = Value::decode(golden_bytes.as_slice())
                .unwrap_or_else(|e| panic!("{name}: golden bytes are not a valid Value: {e}"));
            Fixture {
                rust_input: m["rust_input"].clone(),
                golden,
                golden_bytes,
                decoded: m["decoded"].clone(),
                name,
            }
        })
        .collect()
}

/// Rust `encode_value` reproduces Python's `Value` exactly (semantically; map
/// order is normalized away by prost's `PartialEq`).
#[test]
fn rust_encode_matches_python_value() {
    for f in load_fixtures() {
        let got = encode_value(&f.rust_input);
        assert_eq!(
            got, f.golden,
            "[{}] encode_value mismatch\n  input:  {}\n  got:    {:?}\n  golden: {:?}",
            f.name, f.rust_input, got, f.golden
        );
    }
}

/// For struct-free values the serialization is byte-deterministic, so Rust's
/// wire bytes must be identical to Python's — the strongest interop guarantee.
#[test]
fn rust_encode_is_byte_identical_for_struct_free_values() {
    let mut checked = 0;
    for f in load_fixtures() {
        if contains_struct(&f.golden) {
            continue;
        }
        let got = encode_value(&f.rust_input).encode_to_vec();
        assert_eq!(
            got, f.golden_bytes,
            "[{}] serialized bytes differ from Python\n  got:    {:02x?}\n  golden: {:02x?}",
            f.name, got, f.golden_bytes
        );
        checked += 1;
    }
    assert!(
        checked >= 10,
        "expected to byte-check many fixtures, only did {checked}"
    );
}

/// Rust can read what Python writes: decoding Python's `Value` yields Python's
/// own `MessageToDict` view (numbers compared as f64).
#[test]
fn rust_decode_matches_python_messagetodict() {
    for f in load_fixtures() {
        let got = decode_value(&f.golden);
        assert!(
            json_eq_f64(&got, &f.decoded),
            "[{}] decode mismatch\n  got:     {}\n  decoded: {}",
            f.name,
            got,
            f.decoded
        );
    }
}

/// Full round-trip through the Rust codec returns the (f64-normalized) input.
#[test]
fn rust_roundtrip_returns_input() {
    for f in load_fixtures() {
        let round = decode_value(&encode_value(&f.rust_input));
        assert!(
            json_eq_f64(&round, &f.rust_input),
            "[{}] round-trip mismatch\n  input: {}\n  round: {}",
            f.name,
            f.rust_input,
            round
        );
    }
}
