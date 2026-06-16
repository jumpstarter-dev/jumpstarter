//! `grpcOptions` — a `dict[str, str | int]` passed verbatim to the gRPC channel.
//!
//! The Python config types it as `dict[str, str | int]`
//! (`config/client.py:127`, `config/exporter.py:178`), while the exporter runtime
//! annotates it `dict[str, str]` and lets values flow through unvalidated
//! (spec §2.5, open question 12). We therefore accept **both** string and integer
//! values in a type-safe tagged-by-shape way.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// A single `grpcOptions` value: either an integer or a string.
///
/// `#[serde(untagged)]` with `Int` first means an unquoted YAML integer (`16384`)
/// deserializes to [`GrpcOptionValue::Int`] while a string (`"y"`, or a quoted
/// `"123"`) falls through to [`GrpcOptionValue::Str`] — matching how
/// `yaml.safe_load` types the same documents in Python.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum GrpcOptionValue {
    Int(i64),
    Str(String),
}

impl From<i64> for GrpcOptionValue {
    fn from(v: i64) -> Self {
        GrpcOptionValue::Int(v)
    }
}

impl From<&str> for GrpcOptionValue {
    fn from(v: &str) -> Self {
        GrpcOptionValue::Str(v.to_string())
    }
}

impl From<String> for GrpcOptionValue {
    fn from(v: String) -> Self {
        GrpcOptionValue::Str(v)
    }
}

/// The `grpcOptions` map. Keys are sorted (`BTreeMap`) for deterministic output.
pub type GrpcOptions = BTreeMap<String, GrpcOptionValue>;
