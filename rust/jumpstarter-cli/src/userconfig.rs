//! User-config (`config.yaml`) helpers shared by `config client` and `login`
//! (`python/.../config/user.py`).

use std::path::{Path, PathBuf};

use jumpstarter_config::{paths, ClientConfig, UserConfig, YamlConfig};

/// Load the user config, creating an empty one (persisted) if absent
/// (`user.py:load_or_create`).
pub fn load_or_create() -> Result<UserConfig, String> {
    let path = paths::user_config_path();
    if path.exists() {
        UserConfig::load(&path).map_err(|e| format!("cannot read user config: {e}"))
    } else {
        let cfg = UserConfig::empty();
        cfg.save(&path)
            .map_err(|e| format!("cannot create user config: {e}"))?;
        Ok(cfg)
    }
}

/// Persist the user config to its canonical path.
pub fn save(user: &UserConfig) -> Result<(), String> {
    user.save(paths::user_config_path())
        .map_err(|e| format!("cannot save user config: {e}"))
}

/// Point `current-client` at `name` (verified to exist) or clear it, persist, and
/// return the selected client config path (`user.py:use_client`).
pub fn use_client(user: &mut UserConfig, name: Option<&str>) -> Result<Option<PathBuf>, String> {
    match name {
        Some(n) => {
            let path = paths::client_config_path(n);
            ClientConfig::load(&path).map_err(|e| format!("client config '{n}' not found: {e}"))?;
            user.config.current_client = Some(n.to_string());
            save(user)?;
            Ok(Some(path))
        }
        None => {
            user.config.current_client = None;
            save(user)?;
            Ok(None)
        }
    }
}

/// Aliases of all client configs in the clients dir (`*.yaml`; alias = name up to
/// the first `.`). Order follows the directory listing, matching Python's
/// `os.listdir`.
pub fn list_client_aliases() -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(paths::client_configs_dir()) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.ends_with(".yaml") {
                if let Some(alias) = paths::alias_from_path(Path::new(name.as_ref())) {
                    out.push(alias);
                }
            }
        }
    }
    out
}
