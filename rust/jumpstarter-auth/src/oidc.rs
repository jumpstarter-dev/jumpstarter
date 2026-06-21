//! Minimal OIDC client (`jumpstarter_cli_common/oidc.py`). Currently implements
//! discovery + the refresh-token grant used by `auth refresh` and `login`; the
//! authorization-code / token-exchange / password grants land with `login`.

use serde::Deserialize;

/// Build a reqwest client honoring `--insecure-tls` and, like Python's
/// `_get_ssl_context`, the `SSL_CERT_FILE` env var (its PEM is added as an extra
/// trust root so HTTPS to a custom-CA endpoint works without `-k`).
pub fn build_http_client(insecure: bool) -> Result<reqwest::Client, String> {
    let mut builder = reqwest::Client::builder().danger_accept_invalid_certs(insecure);
    if !insecure {
        if let Ok(path) = std::env::var("SSL_CERT_FILE") {
            if let Ok(pem) = std::fs::read(&path) {
                for cert in reqwest::Certificate::from_pem_bundle(&pem).unwrap_or_default() {
                    builder = builder.add_root_certificate(cert);
                }
            }
        }
    }
    builder.build().map_err(|e| e.to_string())
}

/// OIDC client parameters (`oidc.Config`).
pub struct OidcConfig {
    pub issuer: String,
    pub client_id: String,
    pub insecure_tls: bool,
    pub offline_access: bool,
}

/// The discovery document fields the CLI uses.
#[derive(Debug, Deserialize)]
pub struct Discovery {
    // Used by the login authorization-code grant.
    #[allow(dead_code)]
    #[serde(default)]
    pub authorization_endpoint: Option<String>,
    pub token_endpoint: String,
}

/// A token endpoint response.
#[derive(Debug, Deserialize)]
pub struct Tokens {
    pub access_token: String,
    #[serde(default)]
    pub refresh_token: Option<String>,
    // Surfaced by the token-exchange grant in login.
    #[allow(dead_code)]
    #[serde(default)]
    pub id_token: Option<String>,
}

impl OidcConfig {
    pub fn new(issuer: impl Into<String>, client_id: impl Into<String>) -> Self {
        Self {
            issuer: issuer.into(),
            client_id: client_id.into(),
            insecure_tls: false,
            offline_access: false,
        }
    }

    fn http_client(&self) -> Result<reqwest::Client, String> {
        build_http_client(self.insecure_tls)
    }

    /// Base scopes plus `offline_access` when requested (`oidc.py:62-77`).
    pub fn scopes(&self) -> Vec<&'static str> {
        let mut scopes = vec!["openid", "profile"];
        if self.offline_access {
            scopes.push("offline_access");
        }
        scopes
    }

    /// `GET {issuer}/.well-known/openid-configuration`.
    pub async fn discover(&self) -> Result<Discovery, String> {
        let url = format!(
            "{}/.well-known/openid-configuration",
            self.issuer.trim_end_matches('/')
        );
        let resp = self
            .http_client()?
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("OIDC discovery failed: {e}"))?;
        if !resp.status().is_success() {
            return Err(format!(
                "OIDC discovery at {url} returned HTTP {}",
                resp.status()
            ));
        }
        resp.json()
            .await
            .map_err(|e| format!("invalid OIDC discovery document: {e}"))
    }

    /// POST a grant to the token endpoint and parse the response.
    async fn fetch_token(
        &self,
        token_endpoint: &str,
        params: &[(&str, &str)],
    ) -> Result<Tokens, String> {
        let resp = self
            .http_client()?
            .post(token_endpoint)
            .form(params)
            .send()
            .await
            .map_err(|e| format!("token request failed: {e}"))?;
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("token endpoint returned HTTP {status}: {body}"));
        }
        resp.json()
            .await
            .map_err(|e| format!("invalid token response: {e}"))
    }

    /// The `refresh_token` grant (`oidc.py:refresh_token_grant`).
    pub async fn refresh_token_grant(&self, refresh_token: &str) -> Result<Tokens, String> {
        let discovery = self.discover().await?;
        let scope = self.scopes().join(" ");
        self.fetch_token(
            &discovery.token_endpoint,
            &[
                ("grant_type", "refresh_token"),
                ("refresh_token", refresh_token),
                ("client_id", &self.client_id),
                ("scope", &scope),
            ],
        )
        .await
    }

    /// The `password` grant (`oidc.py:password_grant`).
    pub async fn password_grant(&self, username: &str, password: &str) -> Result<Tokens, String> {
        let discovery = self.discover().await?;
        let scope = self.scopes().join(" ");
        self.fetch_token(
            &discovery.token_endpoint,
            &[
                ("grant_type", "password"),
                ("username", username),
                ("password", password),
                ("client_id", &self.client_id),
                ("scope", &scope),
            ],
        )
        .await
    }

    /// The RFC 8693 token-exchange grant, exchanging an id_token for an access
    /// token (`oidc.py:token_exchange_grant`). `connector_id` is a Dex extension.
    pub async fn token_exchange_grant(
        &self,
        token: &str,
        connector_id: Option<&str>,
    ) -> Result<Tokens, String> {
        let discovery = self.discover().await?;
        let scope = self.scopes().join(" ");
        let mut params = vec![
            (
                "grant_type",
                "urn:ietf:params:oauth:grant-type:token-exchange",
            ),
            (
                "requested_token_type",
                "urn:ietf:params:oauth:token-type:access_token",
            ),
            (
                "subject_token_type",
                "urn:ietf:params:oauth:token-type:id_token",
            ),
            ("subject_token", token),
            ("client_id", &self.client_id),
            ("scope", &scope),
        ];
        if let Some(cid) = connector_id {
            params.push(("connector_id", cid));
        }
        self.fetch_token(&discovery.token_endpoint, &params).await
    }

    /// The browser authorization-code grant (`oidc.py:authorization_code_grant`):
    /// spin up a localhost callback server, print the authorization URL, await the
    /// redirect, then exchange the code. No PKCE (matching the Python authlib usage).
    pub async fn authorization_code_grant(
        &self,
        callback_port: Option<u16>,
    ) -> Result<Tokens, String> {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let discovery = self.discover().await?;
        let auth_endpoint = discovery
            .authorization_endpoint
            .as_deref()
            .ok_or("OIDC discovery document is missing authorization_endpoint")?;

        let port = resolve_callback_port(callback_port)?;
        let listener = TcpListener::bind(("127.0.0.1", port))
            .await
            .map_err(|e| format!("Failed to start callback server on port {port}: {e}"))?;
        let actual_port = listener.local_addr().map_err(|e| e.to_string())?.port();
        let redirect_uri = format!("http://localhost:{actual_port}/callback");

        let state = random_state();
        let scope = self.scopes().join(" ");
        let auth_url = reqwest::Url::parse_with_params(
            auth_endpoint,
            &[
                ("response_type", "code"),
                ("client_id", self.client_id.as_str()),
                ("redirect_uri", redirect_uri.as_str()),
                ("scope", scope.as_str()),
                ("state", state.as_str()),
            ],
        )
        .map_err(|e| format!("invalid authorization endpoint: {e}"))?;

        // `print("...: ", uri)` renders two spaces before the URL.
        println!("Please open the URL in browser:  {auth_url}");

        let (mut stream, _) = listener
            .accept()
            .await
            .map_err(|e| format!("callback server error: {e}"))?;
        let mut buf = vec![0u8; 16384];
        let n = stream.read(&mut buf).await.map_err(|e| e.to_string())?;
        let request = String::from_utf8_lossy(&buf[..n]);
        let target = request
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("");
        let query = target.split_once('?').map(|(_, q)| q).unwrap_or("");

        let body = "Login successful, you can close this page";
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let _ = stream.write_all(response.as_bytes()).await;

        let parsed = reqwest::Url::parse(&format!("http://localhost/?{query}"))
            .map_err(|e| format!("invalid callback URL: {e}"))?;
        let mut code = None;
        let mut got_state = None;
        for (k, v) in parsed.query_pairs() {
            match k.as_ref() {
                "code" => code = Some(v.into_owned()),
                "state" => got_state = Some(v.into_owned()),
                "error" => return Err(format!("authorization failed: {v}")),
                _ => {}
            }
        }
        let code = code.ok_or("callback did not include an authorization code")?;
        if got_state.as_deref() != Some(state.as_str()) {
            return Err("authorization state mismatch (possible CSRF)".to_string());
        }

        self.fetch_token(
            &discovery.token_endpoint,
            &[
                ("grant_type", "authorization_code"),
                ("code", &code),
                ("redirect_uri", &redirect_uri),
                ("client_id", &self.client_id),
            ],
        )
        .await
    }
}

/// Resolve the callback port: explicit flag, else `JMP_OIDC_CALLBACK_PORT`, else 0
/// (OS-assigned) (`oidc.py:127-137`).
fn resolve_callback_port(callback_port: Option<u16>) -> Result<u16, String> {
    if let Some(p) = callback_port {
        return Ok(p);
    }
    match std::env::var(jumpstarter_config::env::JMP_OIDC_CALLBACK_PORT) {
        Err(_) => Ok(0),
        Ok(v) => v.parse::<u16>().map_err(|_| {
            format!(
                "Invalid {} \"{v}\": must be a valid port",
                jumpstarter_config::env::JMP_OIDC_CALLBACK_PORT
            )
        }),
    }
}

/// A random URL-safe state value for CSRF protection.
fn random_state() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("OS RNG unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
