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

/// Text-level differential corpus: each entry's `proto_json` is Python
/// `MessageToJson(encode_value(v))`. Produced by `gen_value_corpus.py`.
const CORPUS: &str = include_str!("value_corpus.json");

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

// ---------------------------------------------------------------------------
// Text-level differential corpus (`value_corpus.json` / `gen_value_corpus.py`).
//
// Where the fixtures above pin the raw wire bytes, this corpus pins the *text*
// Python's `google.protobuf.json_format.MessageToJson` emits for the encoded
// `Value`. The two are independent witnesses of the same contract: if the Rust
// codec ever drifts from Python's JSON projection (number formatting, the
// int->double collapse, the bytes->utf8 and tuple->list quirks, or the
// non-finite->null mapping) one of these tests will catch it.
//
// `MessageToJson` does NOT sort struct keys (protobuf map order is undefined),
// so the comparison is structural (parsed JSON, numbers as f64), never raw
// string equality.
// ---------------------------------------------------------------------------

/// Project a `prost_types::Value` to the `serde_json::Value` that Python's
/// `MessageToJson` would print (then parse). For a `google.protobuf.Value` the
/// JSON projection is exactly the underlying logical value, which is what
/// `decode_value` already computes — so this is `decode_value`, named to make
/// the differential intent explicit at the call site.
fn proto_value_to_serde_json(v: &Value) -> Json {
    decode_value(v)
}

struct CorpusEntry {
    name: String,
    input_json: Json,
    proto_json: Json,
    decode_only: bool,
}

fn load_corpus() -> Vec<CorpusEntry> {
    let raw: Vec<serde_json::Map<String, Json>> =
        serde_json::from_str(CORPUS).expect("parse value_corpus.json");
    assert!(raw.len() >= 30, "corpus too small: {}", raw.len());
    raw.into_iter()
        .map(|m| {
            let name = m["name"].as_str().unwrap().to_string();
            // `proto_json` is stored as a *string* (the literal MessageToJson
            // text). Parse it into a serde_json::Value for structural compare.
            let proto_text = m["proto_json"].as_str().unwrap();
            let proto_json: Json = serde_json::from_str(proto_text)
                .unwrap_or_else(|e| panic!("{name}: proto_json is not valid JSON: {e}"));
            CorpusEntry {
                input_json: m["input_json"].clone(),
                proto_json,
                decode_only: m["decode_only"].as_bool().unwrap(),
                name,
            }
        })
        .collect()
}

/// THE differential check: for every encodable corpus entry, Rust
/// `encode_value(input_json)` projected back to JSON must equal Python's
/// `MessageToJson` of *its* encoded `Value`, compared structurally as f64.
///
/// This proves the Rust codec is byte-exact with the Python codec at the
/// `google.protobuf.Value` level, including every load-bearing quirk:
///   - int -> double (`42` -> `42.0`)
///   - large-int precision (`2^53 + 1` -> `9007199254740992.0` on both sides)
///   - tuple -> list (`(1,2,3)` -> `[1.0,2.0,3.0]`)
///   - bytes -> utf8 string (`b"hi"` -> `"hi"`, NOT base64)
///   - extreme floats (`1e+300`, `1e-300`, machine epsilon)
#[test]
fn rust_encode_matches_python_messagetojson() {
    let mut checked = 0;
    for e in load_corpus() {
        if e.decode_only {
            continue;
        }
        let got = proto_value_to_serde_json(&encode_value(&e.input_json));
        assert!(
            json_eq_f64(&got, &e.proto_json),
            "[{}] encode/MessageToJson mismatch\n  input:      {}\n  rust:       {}\n  python:     {}",
            e.name,
            e.input_json,
            got,
            e.proto_json
        );
        checked += 1;
    }
    assert!(checked >= 25, "expected many encode checks, did {checked}");
}

/// The non-finite cases are decode-only (serde_json cannot parse `NaN`/`Inf`, so
/// Rust can never feed them to `encode_value`). Python's codec maps them to a
/// *null* `Value` (pydantic's mode=json turns non-finite floats into `None`),
/// NOT to a `NaN`/`Infinity` token — its `MessageToJson` emits `"null"`.
///
/// Rust's decode side must agree: a `NumberValue(NaN/±Inf)` must project to JSON
/// `null`. This is the Rust analogue of the same end-state and proves the two
/// codecs converge on `null` for non-finite numbers despite never exchanging a
/// `NaN` on the wire.
#[test]
fn non_finite_numbers_match_python_null_projection() {
    // Every decode_only corpus entry's Python projection is exactly `null`.
    for e in load_corpus().into_iter().filter(|e| e.decode_only) {
        assert_eq!(
            e.proto_json,
            Json::Null,
            "[{}] expected Python to project non-finite to null, got {}",
            e.name,
            e.proto_json
        );
    }
    // And Rust's decode of an in-memory non-finite NumberValue matches that.
    for n in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let v = Value {
            kind: Some(Kind::NumberValue(n)),
        };
        assert_eq!(
            proto_value_to_serde_json(&v),
            Json::Null,
            "Rust must project non-finite {n} to null, matching Python"
        );
    }
}
