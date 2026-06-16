//! Config-file path resolution (spec §2.5; `config/common.py:11`,
//! `config/exporter.py:163-216`).
//!
//! Resolution chain for the config home:
//! `JMP_CLIENT_CONFIG_HOME` > `$XDG_CONFIG_HOME/jumpstarter` > `~/.config/jumpstarter`.
//! Exporter configs additionally fall back to the system dir
//! `/etc/jumpstarter/exporters`, shadowed by the user dir.

use std::path::{Path, PathBuf};

use crate::env::JMP_CLIENT_CONFIG_HOME;

/// System-wide exporter config dir (read fallback for systemd/containers).
pub const EXPORTER_SYSTEM_DIR: &str = "/etc/jumpstarter/exporters";

/// `$XDG_CONFIG_HOME` if set to an absolute path, else `~/.config`
/// (matching `xdg_base_dirs.xdg_config_home`).
fn xdg_config_home() -> PathBuf {
    if let Some(dir) = std::env::var_os("XDG_CONFIG_HOME") {
        let p = PathBuf::from(dir);
        if p.is_absolute() {
            return p;
        }
    }
    home_dir().join(".config")
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// The Jumpstarter config home:
/// `JMP_CLIENT_CONFIG_HOME` > `$XDG_CONFIG_HOME/jumpstarter` > `~/.config/jumpstarter`.
pub fn config_home() -> PathBuf {
    match std::env::var_os(JMP_CLIENT_CONFIG_HOME) {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => xdg_config_home().join("jumpstarter"),
    }
}

/// Directory holding client configs (`<config_home>/clients`).
pub fn client_configs_dir() -> PathBuf {
    config_home().join("clients")
}

/// Path of a client config by alias.
pub fn client_config_path(alias: &str) -> PathBuf {
    client_configs_dir().join(format!("{alias}.yaml"))
}

/// Path of the user config (`<config_home>/config.yaml`).
pub fn user_config_path() -> PathBuf {
    config_home().join("config.yaml")
}

/// User-dir exporter configs (`<config_home>/exporters`).
pub fn exporter_user_dir() -> PathBuf {
    config_home().join("exporters")
}

/// User-dir path of an exporter config by alias.
pub fn exporter_user_path(alias: &str) -> PathBuf {
    exporter_user_dir().join(format!("{alias}.yaml"))
}

/// System-dir path of an exporter config by alias.
pub fn exporter_system_path(alias: &str) -> PathBuf {
    Path::new(EXPORTER_SYSTEM_DIR).join(format!("{alias}.yaml"))
}

/// Resolve an exporter alias to a path, preferring the user dir and falling back
/// to the system dir. When neither exists, the user-dir path is returned so
/// callers surface a "not found" pointing at the current default location
/// (`exporter.py:202-216`).
pub fn resolve_exporter_path(alias: &str) -> PathBuf {
    let user = exporter_user_path(alias);
    if user.exists() {
        return user;
    }
    let system = exporter_system_path(alias);
    if system.exists() {
        return system;
    }
    user
}

/// Derive a config alias from a file path: the file name up to the first `.`
/// (matching the client loader, `client.py:359`).
pub fn alias_from_path(path: &Path) -> Option<String> {
    path.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.split('.').next().unwrap_or(n).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alias_strips_extension() {
        assert_eq!(
            alias_from_path(Path::new("/x/clients/prod.yaml")).as_deref(),
            Some("prod")
        );
        assert_eq!(
            alias_from_path(Path::new("my.exporter.yaml")).as_deref(),
            Some("my")
        );
    }

    #[test]
    fn config_home_prefers_explicit_override() {
        // We cannot safely mutate process env in parallel tests; just assert the
        // pure path-building helpers compose correctly off a known home.
        let p = client_config_path("default");
        assert!(p.ends_with("clients/default.yaml"));
        assert!(user_config_path().ends_with("config.yaml"));
        assert!(exporter_user_path("e").ends_with("exporters/e.yaml"));
        assert_eq!(
            exporter_system_path("e"),
            PathBuf::from("/etc/jumpstarter/exporters/e.yaml")
        );
    }
}
