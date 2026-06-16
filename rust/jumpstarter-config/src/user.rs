//! `UserConfigV1Alpha1` — the CLI's `config.yaml`
//! (`python/packages/jumpstarter/jumpstarter/config/user.py`).
//!
//! On the wire `current-client` is just the selected client's alias (a string) or
//! `null` — Python stores a resolved `ClientConfig` internally but serializes only
//! the alias via a `PlainSerializer` (`user.py:13-35`). We model the serialized
//! shape directly.

use serde::{Deserialize, Serialize};

fn api_version() -> String {
    "jumpstarter.dev/v1alpha1".to_string()
}
fn kind() -> String {
    "UserConfig".to_string()
}

/// The `config:` block (`user.py:48-51`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct UserConfigInner {
    /// Alias of the currently-selected client, or `null`. Serialized as `null`
    /// when unset (matching the on-disk `config.yaml`), and optional on load.
    #[serde(rename = "current-client", default)]
    pub current_client: Option<String>,
}

/// The user configuration for the Jumpstarter CLI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UserConfig {
    #[serde(rename = "apiVersion", default = "api_version")]
    pub api_version: String,
    #[serde(default = "kind")]
    pub kind: String,
    pub config: UserConfigInner,
}

impl Default for UserConfig {
    fn default() -> Self {
        Self {
            api_version: api_version(),
            kind: kind(),
            config: UserConfigInner::default(),
        }
    }
}

impl UserConfig {
    /// A fresh user config with no client selected.
    pub fn empty() -> Self {
        Self::default()
    }

    /// The selected client alias, if any.
    pub fn current_client(&self) -> Option<&str> {
        self.config.current_client.as_deref()
    }
}
