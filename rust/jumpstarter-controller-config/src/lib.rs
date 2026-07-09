//! Pure-data models of the `jumpstarter-controller` ConfigMap contents,
//! mirroring `controller/internal/config/types.go` (`config` key) and the
//! `router` endpoint map. Also hosts the env-var name constants shared by the
//! manager and router binaries.

pub mod duration;
pub mod env;
pub mod jwt_authenticator;
pub mod router;
mod serde_util;
pub mod types;

/// Name of the ConfigMap the operator writes and the controller/router read
/// (`createConfigMap` in the operator; `LoadConfiguration` /
/// `LoadRouterConfiguration` in `controller/internal/config/config.go`).
pub const CONFIG_MAP_NAME: &str = "jumpstarter-controller";

/// ConfigMap data key holding the [`types::Config`] YAML.
pub const CONFIG_MAP_KEY_CONFIG: &str = "config";

/// ConfigMap data key holding the [`router::Router`] YAML.
pub const CONFIG_MAP_KEY_ROUTER: &str = "router";

/// ConfigMap data key of the legacy (pre-"config") authentication section.
/// Go `LoadConfiguration` still honors it for backwards compatibility
/// ("TODO: remove in 0.7.0").
pub const CONFIG_MAP_KEY_LEGACY_AUTHENTICATION: &str = "authentication";
