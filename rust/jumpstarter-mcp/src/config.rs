//! Client-config resolution + controller connection for the MCP server.
//!
//! Mirrors the Python `ClientConnection.resolve()` the MCP used: the `JMP_*`
//! environment, else the user config's `current-client`. The controller session
//! itself runs on the Rust core (`jumpstarter_core::ControllerSession`).

use jumpstarter_config::{client_from_env, paths, ClientConfig, UserConfig, YamlConfig};
use jumpstarter_core::ControllerSession;

/// Resolve the client config the same way the `jmp` CLI does (env, else current-client).
pub fn resolve_client_config() -> Result<ClientConfig, String> {
    if let Some(cfg) = client_from_env() {
        return Ok(cfg);
    }
    let user_path = paths::user_config_path();
    if !user_path.exists() {
        return Err(
            "No jumpstarter client config found. Run 'jmp config client use <name>' or set \
             JMP_* environment variables."
                .to_string(),
        );
    }
    let user = UserConfig::load(&user_path).map_err(|e| format!("Failed to load user config: {e}"))?;
    match user.current_client() {
        Some(alias) => ClientConfig::load(&paths::client_config_path(alias))
            .map_err(|e| format!("Failed to load client config: {e}")),
        None => Err(
            "No current client configured. Run 'jmp config client use <name>' or set JMP_* \
             environment variables."
                .to_string(),
        ),
    }
}

/// Resolve config and connect a controller session on the Rust core.
pub async fn connect() -> Result<ControllerSession, String> {
    let cfg = resolve_client_config()?;
    ControllerSession::connect(
        cfg.endpoint.unwrap_or_default(),
        cfg.token,
        cfg.tls.ca,
        cfg.tls.insecure,
        cfg.metadata.namespace.unwrap_or_default(),
        cfg.metadata.name,
    )
    .await
    .map_err(|e| format!("Failed to connect to controller: {e}"))
}
