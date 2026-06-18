//! Plain-data DTOs that cross the foreign-host boundary.
//!
//! Binding-agnostic (no uniffi/tonic attrs): the per-binding crates mirror them as their
//! own native records (UniFFI `Record`, C struct) and convert. The driver tree is carried
//! **flat** — one [`DriverNode`] per instance, linked by `parent_uuid` — matching both the
//! proto `DriverInstanceReport` and the Python `Driver.enumerate()` output, and avoiding
//! recursive records at the FFI boundary. [`crate::report`] turns these into the proto
//! `GetReportResponse`.

use std::collections::HashMap;

/// A single driver instance as introspected by the foreign host. The labels already
/// include `jumpstarter.dev/client` (the client class import path) and any driver labels;
/// `methods_description` maps `@export` method name → help text. `parent_uuid` is `None`
/// for the root and the parent's uuid for a child.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DriverNode {
    pub uuid: String,
    pub parent_uuid: Option<String>,
    pub labels: HashMap<String, String>,
    pub description: Option<String>,
    pub methods_description: HashMap<String, String>,
}

impl DriverNode {
    /// Convenience constructor for a root node (no parent).
    pub fn root(
        uuid: impl Into<String>,
        labels: HashMap<String, String>,
        description: Option<String>,
        methods_description: HashMap<String, String>,
    ) -> Self {
        Self {
            uuid: uuid.into(),
            parent_uuid: None,
            labels,
            description,
            methods_description,
        }
    }
}
