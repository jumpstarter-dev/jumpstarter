//! `ObjectMeta` — the `metadata` block shared by client and exporter configs
//! (`python/packages/jumpstarter/jumpstarter/config/common.py:15-17`).

use serde::{Deserialize, Serialize};

/// Identity metadata for a config: namespace + name.
///
/// In Python `namespace: str | None` is a *required* field (pydantic v2) that is
/// normally satisfied from the YAML or from the `JMP_NAMESPACE` env var
/// (`ObjectMeta` is a `BaseSettings` with `env_prefix="JMP_"`). Here it is modelled
/// as optional (defaulting to `None`, omitted when absent) so that
/// load→save→load round-trips are total even for configs that carry no namespace —
/// a deliberate leniency over Python's strict requirement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObjectMeta {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub namespace: Option<String>,
    pub name: String,
}

impl ObjectMeta {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            namespace: None,
            name: name.into(),
        }
    }

    pub fn with_namespace(mut self, namespace: impl Into<String>) -> Self {
        self.namespace = Some(namespace.into());
        self
    }
}
