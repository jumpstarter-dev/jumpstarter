//! Environment-variable names and the client-from-environment builder.
//!
//! Client configs carry env overrides (`JMP_*`, `JMP_DRIVERS_*`); exporter configs
//! do **not** (spec §2.5). The Python `ClientConfig` is a `BaseSettings` with
//! `env_prefix="JMP_"`, so a client can be built purely from the environment when
//! no named config file is selected (`client.py:369-377`).

use crate::client::ClientConfig;
use crate::meta::ObjectMeta;

// Names mirror `python/.../config/env.py`.
pub const JMP_CLIENT_CONFIG_HOME: &str = "JMP_CLIENT_CONFIG_HOME";
pub const JMP_CLIENT_CONFIG: &str = "JMP_CLIENT_CONFIG";
pub const JMP_NAMESPACE: &str = "JMP_NAMESPACE";
pub const JMP_NAME: &str = "JMP_NAME";
pub const JMP_ENDPOINT: &str = "JMP_ENDPOINT";
pub const JMP_TOKEN: &str = "JMP_TOKEN";
pub const JMP_DRIVERS_ALLOW: &str = "JMP_DRIVERS_ALLOW";
pub const JMP_DRIVERS_UNSAFE: &str = "JMP_DRIVERS_UNSAFE";
pub const JUMPSTARTER_HOST: &str = "JUMPSTARTER_HOST";
pub const JMP_LEASE: &str = "JMP_LEASE";
pub const JMP_DISABLE_COMPRESSION: &str = "JMP_DISABLE_COMPRESSION";
pub const JMP_OIDC_CALLBACK_PORT: &str = "JMP_OIDC_CALLBACK_PORT";
pub const JMP_GRPC_INSECURE: &str = "JMP_GRPC_INSECURE";
pub const JUMPSTARTER_GRPC_INSECURE: &str = "JUMPSTARTER_GRPC_INSECURE";
pub const JMP_GRPC_PASSPHRASE: &str = "JMP_GRPC_PASSPHRASE";

fn parse_bool(v: &str) -> bool {
    matches!(
        v.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Build a [`ClientConfig`] from an injected environment lookup.
///
/// Returns `None` when `JMP_NAME` is absent (the name is required; Python's
/// `try_from_env` likewise yields `None` on the resulting validation error).
/// `JMP_DRIVERS_ALLOW` is comma-split, and a literal `UNSAFE` entry — or a truthy
/// `JMP_DRIVERS_UNSAFE` — sets `drivers.unsafe` (`client.py:78-91`).
pub fn client_from_env_with<F>(get: F) -> Option<ClientConfig>
where
    F: Fn(&str) -> Option<String>,
{
    let name = get(JMP_NAME)?;
    let mut config = ClientConfig::new(ObjectMeta {
        namespace: get(JMP_NAMESPACE),
        name,
    });
    config.endpoint = get(JMP_ENDPOINT);
    config.token = get(JMP_TOKEN);

    let allow: Vec<String> = match get(JMP_DRIVERS_ALLOW) {
        Some(s) if !s.is_empty() => s.split(',').map(|p| p.to_string()).collect(),
        _ => Vec::new(),
    };
    let unsafe_flag = get(JMP_DRIVERS_UNSAFE).as_deref().map(parse_bool) == Some(true)
        || allow.iter().any(|a| a == "UNSAFE");
    config.drivers.allow = allow;
    config.drivers.r#unsafe = unsafe_flag;

    Some(config)
}

/// Build a [`ClientConfig`] from the process environment.
pub fn client_from_env() -> Option<ClientConfig> {
    client_from_env_with(|k| std::env::var(k).ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn getter<'a>(map: &'a HashMap<&str, &str>) -> impl Fn(&str) -> Option<String> + 'a {
        move |k| map.get(k).map(|v| v.to_string())
    }

    #[test]
    fn requires_name() {
        let env: HashMap<&str, &str> = HashMap::new();
        assert!(client_from_env_with(getter(&env)).is_none());
    }

    #[test]
    fn builds_from_env_with_driver_overrides() {
        let env = HashMap::from([
            (JMP_NAME, "ci"),
            (JMP_NAMESPACE, "lab"),
            (JMP_ENDPOINT, "grpc:8082"),
            (JMP_TOKEN, "tok"),
            (JMP_DRIVERS_ALLOW, "a.b.*,c.d.*"),
        ]);
        let c = client_from_env_with(getter(&env)).expect("name present");
        assert_eq!(c.metadata.name, "ci");
        assert_eq!(c.metadata.namespace.as_deref(), Some("lab"));
        assert_eq!(c.endpoint.as_deref(), Some("grpc:8082"));
        assert_eq!(c.token.as_deref(), Some("tok"));
        assert_eq!(c.drivers.allow, vec!["a.b.*", "c.d.*"]);
        assert!(!c.drivers.r#unsafe);
    }

    #[test]
    fn unsafe_via_allow_sentinel_or_flag() {
        let env = HashMap::from([(JMP_NAME, "x"), (JMP_DRIVERS_ALLOW, "UNSAFE")]);
        assert!(client_from_env_with(getter(&env)).unwrap().drivers.r#unsafe);

        let env = HashMap::from([(JMP_NAME, "x"), (JMP_DRIVERS_UNSAFE, "true")]);
        assert!(client_from_env_with(getter(&env)).unwrap().drivers.r#unsafe);
    }
}
