//! Client-config resolution + controller connection for the MCP server.
//!
//! Mirrors the Python `ClientConnection.resolve()` + `_ensure_fresh_token` the MCP used: the
//! `JMP_*` environment, else the user config's `current-client`; before each controller
//! connection the access token is refreshed via OIDC if it is expired or near expiry. The
//! controller session itself runs on the Rust core (`jumpstarter_client::ControllerSession`).

use std::path::{Path, PathBuf};

use jumpstarter_client::ControllerSession;
use jumpstarter_config::{client_from_env, paths, ClientConfig, UserConfig, YamlConfig};

/// Tokens within this many seconds of expiry are refreshed (matches the Python MCP's
/// `TOKEN_REFRESH_THRESHOLD_SECONDS`).
const TOKEN_REFRESH_THRESHOLD_SECS: i64 = 30;

/// Resolve the client config the same way the `jmp` CLI does (env, else current-client),
/// returning its on-disk path (`None` for an env-built config) so a refreshed token can be
/// written back.
pub fn resolve_with_path() -> Result<(ClientConfig, Option<PathBuf>), String> {
    if let Some(cfg) = client_from_env() {
        return Ok((cfg, None));
    }
    let user_path = paths::user_config_path();
    if !user_path.exists() {
        return Err(
            "No jumpstarter client config found. Run 'jmp config client use <name>' or set \
                    JMP_* environment variables."
                .to_string(),
        );
    }
    let user =
        UserConfig::load(&user_path).map_err(|e| format!("Failed to load user config: {e}"))?;
    match user.current_client() {
        Some(alias) => {
            let path = paths::client_config_path(alias);
            let cfg = ClientConfig::load(&path)
                .map_err(|e| format!("Failed to load client config: {e}"))?;
            Ok((cfg, Some(path)))
        }
        None => Err(
            "No current client configured. Run 'jmp config client use <name>' or set JMP_* \
                     environment variables."
                .to_string(),
        ),
    }
}

/// Refresh the access token if it is expired or within the threshold of expiry, mirroring the
/// Python MCP `_ensure_fresh_token`: decode the JWT, and if stale, use the stored refresh_token
/// to obtain a new access token via OIDC (client_id `jumpstarter-cli`) and persist it back to the
/// config file. Best-effort — on any failure it logs and leaves the token unchanged (the
/// downstream controller call then surfaces the error), exactly like the Python implementation.
async fn ensure_fresh_token(config: &mut ClientConfig, path: Option<&Path>) {
    let token = match config.token.as_deref() {
        Some(t) if !t.is_empty() => t.to_string(),
        _ => return,
    };
    if !jumpstarter_auth::jwt::is_stale(&token, TOKEN_REFRESH_THRESHOLD_SECS) {
        return;
    }
    let refresh_token = match config.refresh_token.as_deref() {
        Some(rt) if !rt.is_empty() => rt.to_string(),
        _ => {
            tracing::warn!(
                "Token is expired but no refresh_token stored - run 'jmp login --offline-access'"
            );
            return;
        }
    };
    let issuer = match jumpstarter_auth::jwt::issuer(&token) {
        Ok(Some(iss)) => iss,
        Ok(None) => {
            tracing::warn!("No issuer in JWT, skipping token refresh");
            return;
        }
        Err(_) => {
            tracing::warn!("Failed to decode JWT issuer, skipping token refresh");
            return;
        }
    };
    tracing::info!("Access token expired or near expiry, attempting refresh via OIDC");
    let oidc = jumpstarter_auth::oidc::OidcConfig::new(issuer, "jumpstarter-cli");
    match oidc.refresh_token_grant(&refresh_token).await {
        Ok(tokens) => {
            config.token = Some(tokens.access_token.clone());
            if tokens.refresh_token.is_some() {
                config.refresh_token = tokens.refresh_token.clone();
            }
            if let Some(path) = path {
                save_token(path, &tokens.access_token, tokens.refresh_token.as_deref());
            }
            tracing::info!("Access token refreshed successfully");
        }
        Err(e) => {
            tracing::warn!("Token refresh failed - downstream call will likely fail: {e}");
        }
    }
}

/// Persist a refreshed token back to the client config file, preserving every other field by
/// reloading + re-saving (Python `ClientConnection.save_token`). Best-effort.
fn save_token(path: &Path, access_token: &str, refresh_token: Option<&str>) {
    match ClientConfig::load(path) {
        Ok(mut cfg) => {
            cfg.token = Some(access_token.to_string());
            if let Some(rt) = refresh_token {
                cfg.refresh_token = Some(rt.to_string());
            }
            if let Err(e) = cfg.save(path) {
                tracing::warn!(
                    "Failed to persist refreshed token to {}: {e}",
                    path.display()
                );
            }
        }
        Err(e) => tracing::warn!("Failed to reload config to save refreshed token: {e}"),
    }
}

/// Resolve config (refreshing the token if stale) and connect a controller session.
pub async fn connect() -> Result<ControllerSession, String> {
    connect_with_config().await.map(|(session, _)| session)
}

/// Like [`connect`], but also returns the resolved config (for its driver allow-list /
/// unsafe flag, which the connection tools propagate to the `j` subprocess environment).
pub async fn connect_with_config() -> Result<(ControllerSession, ClientConfig), String> {
    let (mut cfg, path) = resolve_with_path()?;
    ensure_fresh_token(&mut cfg, path.as_deref()).await;
    let session = ControllerSession::connect(
        cfg.endpoint.clone().unwrap_or_default(),
        cfg.token.clone(),
        cfg.tls.ca.clone(),
        cfg.tls.insecure,
        cfg.metadata.namespace.clone().unwrap_or_default(),
        cfg.metadata.name.clone(),
    )
    .await
    .map_err(|e| format!("Failed to connect to controller: {e}"))?;
    Ok((session, cfg))
}
