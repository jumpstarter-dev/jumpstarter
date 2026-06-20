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

// Ported from the deleted Python `config/user_config_test.py`: `current-client`
// serializes as the bare alias (or null), and round-trips.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::YamlConfig;

    #[test]
    fn empty_serializes_current_client_as_null() {
        let u = UserConfig::empty();
        let yaml = u.to_yaml().unwrap();
        assert!(yaml.contains("current-client: null"), "{yaml}");
        assert!(yaml.contains("apiVersion:"));
        assert_eq!(u.current_client(), None);
    }

    #[test]
    fn parses_and_exposes_current_client_alias() {
        let yaml = "apiVersion: jumpstarter.dev/v1alpha1\n\
kind: UserConfig\n\
config:\n  current-client: my-client\n";
        let u = UserConfig::from_yaml(yaml).unwrap();
        assert_eq!(u.current_client(), Some("my-client"));
    }

    #[test]
    fn round_trips() {
        let mut u = UserConfig::empty();
        u.config.current_client = Some("c1".to_string());
        let reparsed = UserConfig::from_yaml(&u.to_yaml().unwrap()).unwrap();
        assert_eq!(u, reparsed);
        assert_eq!(reparsed.current_client(), Some("c1"));
    }

    #[test]
    fn missing_current_client_defaults_to_none() {
        let yaml = "apiVersion: jumpstarter.dev/v1alpha1\nkind: UserConfig\nconfig: {}\n";
        let u = UserConfig::from_yaml(yaml).unwrap();
        assert_eq!(u.current_client(), None);
    }
}
