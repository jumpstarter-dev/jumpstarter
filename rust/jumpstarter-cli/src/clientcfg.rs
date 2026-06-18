//! Client-config resolution shared by the controller resource and auth commands
//! (`jumpstarter_cli_common/config.py:opt_config`, client side only).

use std::path::PathBuf;

use clap::Args as ClapArgs;
use jumpstarter_client::ControllerClient;
use jumpstarter_config::{client_from_env, paths, ClientConfig, UserConfig, YamlConfig};

use crate::cmderr::CmdError;

/// The `--client` / `--client-config` selector options (`opt_config(client=True)`).
#[derive(ClapArgs, Clone, Default)]
pub struct ConfigOpts {
    /// Alias of client config.
    #[arg(long, global = true)]
    pub client: Option<String>,
    /// Path to client config.
    #[arg(long = "client-config", global = true)]
    pub client_config: Option<PathBuf>,
}

impl ConfigOpts {
    /// Resolve to a concrete [`ClientConfig`] following the `opt_config`
    /// precedence: explicit `--client`/`--client-config`, else the `JMP_*`
    /// environment, else the user config's current client.
    pub fn resolve(&self) -> Result<ClientConfig, CmdError> {
        self.resolve_with_path().map(|(config, _)| config)
    }

    /// Like [`resolve`](Self::resolve) but also returns the path the config should
    /// be saved back to (for `auth refresh`/`rotate`). An environment-built config
    /// maps to the `default` client file (Python `_get_path("default")`).
    pub fn resolve_with_path(&self) -> Result<(ClientConfig, PathBuf), CmdError> {
        let load = |path: PathBuf| -> Result<(ClientConfig, PathBuf), CmdError> {
            // Match Python's `ClientConfigV1Alpha1.load` FileNotFound message.
            if !path.exists() {
                return Err(CmdError::Runtime(format!(
                    "Failed to load config: Client config '{}' does not exist.",
                    path.display()
                )));
            }
            match ClientConfig::load(&path) {
                Ok(c) => Ok((c, path)),
                Err(e) => Err(CmdError::Runtime(format!("Failed to load config: {e}"))),
            }
        };
        match (&self.client, &self.client_config) {
            (Some(_), Some(_)) => Err(CmdError::Usage(
                "only one of --client, --client-config should be specified".to_string(),
            )),
            (Some(alias), None) => load(paths::client_config_path(alias)),
            (None, Some(path)) => load(path.clone()),
            (None, None) => {
                if let Some(cfg) = client_from_env() {
                    return Ok((cfg, paths::client_config_path("default")));
                }
                let user = load_or_create_user()?;
                match user.current_client() {
                    Some(alias) => load(paths::client_config_path(alias)),
                    None => Err(CmdError::Runtime(
                        "none of --client, --client-config is specified, and default config is not set"
                            .to_string(),
                    )),
                }
            }
        }
    }

    /// Resolve and connect to the controller, returning both the config (for its
    /// `metadata.name`/`namespace`) and the connected client. Used by `auth rotate`.
    #[allow(dead_code)]
    pub async fn connect(&self) -> Result<(ClientConfig, ControllerClient), CmdError> {
        let config = self.resolve()?;
        let controller = ControllerClient::connect(&config)
            .await
            .map_err(|e| CmdError::Runtime(e.to_string()))?;
        Ok((config, controller))
    }
}

fn load_or_create_user() -> Result<UserConfig, CmdError> {
    let path = paths::user_config_path();
    if path.exists() {
        UserConfig::load(&path)
            .map_err(|e| CmdError::Runtime(format!("Failed to load config: {e}")))
    } else {
        let cfg = UserConfig::empty();
        cfg.save(&path)
            .map_err(|e| CmdError::Runtime(format!("Failed to load config: {e}")))?;
        Ok(cfg)
    }
}
