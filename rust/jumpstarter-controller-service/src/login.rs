//! The login HTTP service (`:8086`), a port of
//! `controller/internal/service/login/service.go`.
//!
//! It serves the CLI-login helper endpoints over **plain HTTP** (Go uses gin on
//! a bare `http.Server`, no TLS):
//!
//! - `GET /v1/auth/config` — the [`AuthConfig`] JSON the `jmp login` flow reads:
//!   the gRPC endpoint, optional router endpoint, namespace, the **base64**-
//!   encoded CA bundle (the Python CLI `b64decode`s the `ca` field), and the
//!   external OIDC provider list;
//! - `GET /healthz` — `200 "ok"`;
//! - `GET /` — a minimal landing page (Go renders an HTML template; the port
//!   keeps a plain-text stand-in since the template is display-only).
//!
//! This service returns `NeedLeaderElection()=false` in Go, so it runs on **all**
//! replicas — the manager starts it outside the leader-gated set.

use std::net::SocketAddr;

use axum::extract::State;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use base64::Engine as _;
use serde::Serialize;

use jumpstarter_controller_config::env;

/// Default login listen address (`login/service.go:34` `defaultPort`).
pub const DEFAULT_LOGIN_ADDR: &str = ":8086";

/// A single external OIDC provider entry, mirroring `login.OIDCConfig`
/// (`service.go:39-43`). `clientId` defaults to `"jumpstarter-cli"` when built
/// from the ConfigMap (`jwtAuthenticatorsToOIDCConfigs`).
#[derive(Clone, Debug, Serialize)]
pub struct OidcConfig {
    pub issuer: String,
    #[serde(rename = "clientId")]
    pub client_id: String,
    #[serde(rename = "audiences", skip_serializing_if = "Vec::is_empty")]
    pub audiences: Vec<String>,
}

/// The runtime configuration of the login service, mirroring `login.Config`
/// (`service.go:56-72`). `ca_bundle` is the **raw** PEM; it is base64-encoded
/// only in the `/v1/auth/config` response.
#[derive(Clone, Debug)]
pub struct LoginConfig {
    pub grpc_endpoint: String,
    pub router_endpoint: String,
    /// Public URL of the login service, shown on the landing page in the
    /// `jmp login your-username@<login_endpoint>` example (Go `Config.LoginEndpoint`
    /// from `LOGIN_ENDPOINT`, `service.go:62`).
    pub login_endpoint: String,
    pub namespace: String,
    pub ca_bundle_pem: String,
    pub oidc: Vec<OidcConfig>,
}

impl LoginConfig {
    /// Port of `NewServiceFromEnv` (`service.go:88-100`): read the endpoints,
    /// namespace and CA bundle from the environment. The OIDC list is supplied
    /// separately (Go's `SetOIDCConfig`, from the ConfigMap).
    pub fn from_env(oidc: Vec<OidcConfig>) -> Self {
        Self {
            grpc_endpoint: env_or(env::GRPC_ENDPOINT, env::DEFAULT_GRPC_ENDPOINT),
            router_endpoint: std::env::var(env::GRPC_ROUTER_ENDPOINT).unwrap_or_default(),
            login_endpoint: std::env::var(env::LOGIN_ENDPOINT).unwrap_or_default(),
            namespace: std::env::var(env::NAMESPACE).unwrap_or_default(),
            ca_bundle_pem: std::env::var(env::CA_BUNDLE_PEM).unwrap_or_default(),
            oidc,
        }
    }
}

fn env_or(key: &str, default: &str) -> String {
    match std::env::var(key) {
        Ok(value) if !value.is_empty() => value,
        _ => default.to_string(),
    }
}

/// The `/v1/auth/config` response body, mirroring `login.AuthConfig`
/// (`service.go:46-53`). `caBundle` is base64; empty optional fields are
/// omitted, matching Go's `omitempty` tags.
#[derive(Serialize)]
struct AuthConfig {
    #[serde(rename = "grpcEndpoint")]
    grpc_endpoint: String,
    #[serde(rename = "routerEndpoint", skip_serializing_if = "String::is_empty")]
    router_endpoint: String,
    namespace: String,
    #[serde(rename = "caBundle", skip_serializing_if = "String::is_empty")]
    ca_bundle: String,
    #[serde(rename = "oidc", skip_serializing_if = "Vec::is_empty")]
    oidc: Vec<OidcConfig>,
}

async fn handle_auth_config(
    State(config): State<std::sync::Arc<LoginConfig>>,
) -> impl IntoResponse {
    // Base64-encode the CA bundle to match what the Python CLI expects
    // (`ssl_channel_credentials` calls `b64decode` on the `ca` field).
    let ca_bundle = if config.ca_bundle_pem.is_empty() {
        String::new()
    } else {
        base64::engine::general_purpose::STANDARD.encode(config.ca_bundle_pem.as_bytes())
    };
    Json(AuthConfig {
        grpc_endpoint: config.grpc_endpoint.clone(),
        router_endpoint: config.router_endpoint.clone(),
        namespace: config.namespace.clone(),
        ca_bundle,
        oidc: config.oidc.clone(),
    })
}

async fn handle_healthz() -> impl IntoResponse {
    "ok"
}

async fn handle_landing(State(config): State<std::sync::Arc<LoginConfig>>) -> impl IntoResponse {
    // Port of Go's `handleLandingPage` (`service.go:179`, template
    // `templates/index.html`): the display-only landing page carrying the
    // `jmp login <user>@<login-endpoint>` instruction plus the service info.
    // The e2e (`e2e_test.go:61`) asserts the body contains "Jumpstarter" and
    // "jmp login"; the fields mirror the Go template's model.
    let router_row = if config.router_endpoint.is_empty() {
        String::new()
    } else {
        format!(
            "<div class=\"info-item\"><span class=\"info-label\">Router Endpoint</span> \
             <span class=\"info-value\">{}</span></div>",
            html_escape(&config.router_endpoint)
        )
    };
    let namespace_row = if config.namespace.is_empty() {
        String::new()
    } else {
        format!(
            "<div class=\"info-item\"><span class=\"info-label\">Namespace</span> \
             <span class=\"info-value\">{}</span></div>",
            html_escape(&config.namespace)
        )
    };
    let body = format!(
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n\
         <title>Jumpstarter Login</title>\n</head>\n<body>\n\
         <div class=\"container\">\n\
         <div class=\"card\">\n<h1>Jumpstarter</h1>\n\
         <p>Hardware-in-the-loop testing platform</p>\n\
         <h2>Quick Login</h2>\n\
         <p>Use the following command to login with your credentials:</p>\n\
         <div class=\"code-block\"><code>jmp login <span class=\"highlight\">your-username</span>@\
         <span class=\"highlight\">{login}</span></code></div>\n\
         <p>Replace <code>your-username</code> with your client name.</p>\n\
         </div>\n\
         <div class=\"card\">\n<h2>Service Information</h2>\n\
         <div class=\"info-item\"><span class=\"info-label\">gRPC Endpoint</span> \
         <span class=\"info-value\">{grpc}</span></div>\n{router_row}\n{namespace_row}\n\
         </div>\n</div>\n</body>\n</html>\n",
        login = html_escape(&config.login_endpoint),
        grpc = html_escape(&config.grpc_endpoint),
    );
    ([(axum::http::header::CONTENT_TYPE, "text/html; charset=utf-8")], body)
}

/// Minimal HTML-escaping for the interpolated endpoint/namespace values (they
/// come from operator config, but are escaped for defense in depth, matching
/// Go's `html/template` auto-escaping).
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Build the axum router for the login endpoints.
pub fn router(config: LoginConfig) -> Router {
    let state = std::sync::Arc::new(config);
    Router::new()
        .route("/", get(handle_landing))
        .route("/v1/auth/config", get(handle_auth_config))
        .route("/healthz", get(handle_healthz))
        .with_state(state)
}

/// Resolve the `LOGIN_SERVICE_PORT` value into a bindable `host:port`, matching
/// Go's normalization (`service.go:130-141`): a valid `host:port` (or
/// `[ipv6]:port`) is kept; a bare port gets a leading `:`; the default is
/// `":8086"`. A host-less `:port` binds all interfaces.
pub fn listen_addr_from_env() -> String {
    let raw = env_or(env::LOGIN_SERVICE_PORT, DEFAULT_LOGIN_ADDR);
    normalize_listen_addr(&raw)
}

fn normalize_listen_addr(raw: &str) -> String {
    if raw.is_empty() {
        return DEFAULT_LOGIN_ADDR.to_string();
    }
    // Already a host:port or [ipv6]:port?
    if raw.parse::<SocketAddr>().is_ok() {
        return raw.to_string();
    }
    // A host-less ":8086" is a valid listen spec (all interfaces).
    if let Some(rest) = raw.strip_prefix(':') {
        if rest.parse::<u16>().is_ok() {
            return raw.to_string();
        }
    }
    // Bare port ("8086") → ":8086".
    if raw.parse::<u16>().is_ok() {
        return format!(":{raw}");
    }
    raw.to_string()
}

/// Serve the login endpoints on `addr` until `shutdown` resolves. A host-less
/// `":8086"` binds all interfaces (IPv6 wildcard, IPv4 fallback), matching the
/// manager's other listeners.
pub async fn serve<S>(config: LoginConfig, addr: &str, shutdown: S) -> std::io::Result<()>
where
    S: std::future::Future<Output = ()> + Send + 'static,
{
    let listener = bind_listen_addr(addr).await?;
    tracing::info!(addr, "serving login service");
    axum::serve(listener, router(config).into_make_service())
        .with_graceful_shutdown(shutdown)
        .await
}

async fn bind_listen_addr(addr: &str) -> std::io::Result<tokio::net::TcpListener> {
    if let Some(port) = addr.strip_prefix(':') {
        match tokio::net::TcpListener::bind(format!("[::]:{port}")).await {
            Ok(listener) => Ok(listener),
            Err(_) => tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await,
        }
    } else {
        tokio::net::TcpListener::bind(addr).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_bare_port_gets_colon() {
        assert_eq!(normalize_listen_addr("8086"), ":8086");
    }

    #[test]
    fn normalize_keeps_host_port() {
        assert_eq!(normalize_listen_addr("127.0.0.1:8086"), "127.0.0.1:8086");
    }

    #[test]
    fn normalize_keeps_colon_port() {
        assert_eq!(normalize_listen_addr(":9000"), ":9000");
    }

    #[test]
    fn normalize_empty_is_default() {
        assert_eq!(normalize_listen_addr(""), DEFAULT_LOGIN_ADDR);
    }

    #[test]
    fn auth_config_base64_encodes_ca_and_omits_empty() {
        let config = LoginConfig {
            grpc_endpoint: "grpc.example:443".into(),
            router_endpoint: String::new(),
            login_endpoint: "login.example:8086".into(),
            namespace: "ns".into(),
            ca_bundle_pem: "PEMDATA".into(),
            oidc: vec![],
        };
        let ca = base64::engine::general_purpose::STANDARD.encode(config.ca_bundle_pem.as_bytes());
        let body = AuthConfig {
            grpc_endpoint: config.grpc_endpoint.clone(),
            router_endpoint: config.router_endpoint.clone(),
            namespace: config.namespace.clone(),
            ca_bundle: ca.clone(),
            oidc: config.oidc.clone(),
        };
        let json = serde_json::to_value(&body).unwrap();
        assert_eq!(json["grpcEndpoint"], "grpc.example:443");
        assert_eq!(json["namespace"], "ns");
        assert_eq!(json["caBundle"], ca);
        // Empty router endpoint + empty oidc omitted.
        assert!(json.get("routerEndpoint").is_none());
        assert!(json.get("oidc").is_none());
    }

    #[tokio::test]
    async fn landing_page_contains_e2e_substrings() {
        // e2e_test.go:61 asserts the landing page body ContainsSubstring
        // "Jumpstarter" AND "jmp login".
        let config = LoginConfig {
            grpc_endpoint: "grpc.example:443".into(),
            router_endpoint: "router.example:8083".into(),
            login_endpoint: "login.example:8086".into(),
            namespace: "ns".into(),
            ca_bundle_pem: String::new(),
            oidc: vec![],
        };
        let resp = handle_landing(State(std::sync::Arc::new(config)))
            .await
            .into_response();
        let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(body.contains("Jumpstarter"), "body: {body}");
        assert!(body.contains("jmp login"), "body: {body}");
        assert!(body.contains("login.example:8086"), "body: {body}");
    }

    #[test]
    fn oidc_config_omits_empty_audiences() {
        let entry = OidcConfig {
            issuer: "https://issuer".into(),
            client_id: "jumpstarter-cli".into(),
            audiences: vec![],
        };
        let json = serde_json::to_value(&entry).unwrap();
        assert_eq!(json["issuer"], "https://issuer");
        assert_eq!(json["clientId"], "jumpstarter-cli");
        assert!(json.get("audiences").is_none());
    }
}
