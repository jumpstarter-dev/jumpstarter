//! The internal OIDC discovery/JWKS HTTPS server, port of the `Register`
//! handlers on the Go `oidc.Signer` (`controller/internal/oidc/op.go:76-87`)
//! as wired by `service.OIDCService` (`cmd/main.go:293-299`) onto a self-signed
//! `localhost` listener (`cmd/main.go:206-210`).
//!
//! Two routes, matching zitadel `op.Discover` / `op.Keys` output:
//!
//! - `GET /.well-known/openid-configuration` →
//!   `{"issuer": <issuer>, "jwks_uri": <issuer>/jwks}` (`op.go:77-82`). The Go
//!   handler builds an `oidc.DiscoveryConfiguration` with only `Issuer` and
//!   `JwksURI` set; every other field is `omitempty`, so the emitted object is
//!   exactly those two keys, in that order (struct-field order), with the
//!   trailing newline `json.Encoder.Encode` appends (zitadel `httphelper`).
//! - `GET /jwks` → the single-key JWKS document
//!   ([`Signer::jwks_document`], `op.go:84-86`).
//!
//! **Behavior-preserving simplification (approved plan, Phase 3):** the Rust
//! controller verifies internal ES256 tokens **in-process** against the
//! in-memory signing key ([`Signer::validate`]); it never fetches this
//! discovery document to authenticate. This server therefore exists purely for
//! **compatibility and debugging** — e.g. an out-of-cluster Kubernetes JWT
//! authenticator or a human inspecting `/.well-known/openid-configuration`.
//! Nothing on the controller's hot path depends on it being reachable.
//!
//! Like Go (`cmd/main.go:206`), a **fresh** self-signed certificate for CN
//! `"jumpstarter oidc"` / DNS `localhost` is generated at startup
//! ([`self_signed_localhost`]); it is never externally trusted, so callers
//! either pin it or skip verification. As in the rest of the workspace this
//! module does not install a rustls `CryptoProvider`; the binaries install the
//! ring provider before serving.

use std::sync::Arc;

use axum::extract::State;
use axum::http::header;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder as ConnBuilder;
use hyper_util::service::TowerToHyperService;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::ServerConfig;
use thiserror::Error;
use time::{Duration, OffsetDateTime};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

use crate::signer::Signer;

/// Bind address of the internal OIDC provider (`cmd/main.go`: the OIDC service
/// listens on `:8085`; the self-signed cert is `localhost`-only).
pub const DISCOVERY_ADDR: &str = "127.0.0.1:8085";
/// Discovery document route (`op.go:77`).
pub const OPENID_CONFIGURATION_PATH: &str = "/.well-known/openid-configuration";
/// JWKS route (`op.go:84`).
pub const JWKS_PATH: &str = "/jwks";
/// Self-signed CN used by the internal OIDC provider (`cmd/main.go:206`).
pub const OIDC_COMMON_NAME: &str = "jumpstarter oidc";

/// Errors starting or serving the discovery server. All are fatal at startup
/// in Go (`cmd/main.go:207-209` / manager exit).
#[derive(Debug, Error)]
pub enum DiscoveryError {
    /// Binding the listen socket failed.
    #[error("failed to bind discovery listener on {addr}: {source}")]
    Bind {
        addr: String,
        #[source]
        source: std::io::Error,
    },
    /// Accepting a connection failed (fatal — ends the serve loop).
    #[error("failed to accept discovery connection: {0}")]
    Accept(#[source] std::io::Error),
    /// rcgen failed generating the self-signed `localhost` certificate.
    #[error("failed to generate self-signed certificate: {0}")]
    SelfSigned(#[from] rcgen::Error),
    /// rustls rejected the certificate/key when building the server config.
    #[error("failed to build rustls server config: {0}")]
    ServerConfig(#[from] rustls::Error),
}

/// Builds the axum router for the two discovery routes, with the [`Signer`] as
/// shared state (it supplies both the issuer and the JWKS body).
pub fn router(signer: Arc<Signer>) -> Router {
    Router::new()
        .route(OPENID_CONFIGURATION_PATH, get(openid_configuration))
        .route(JWKS_PATH, get(jwks))
        .with_state(signer)
}

/// Serializes the discovery document exactly as Go's `op.Discover` emits it:
/// `{"issuer":...,"jwks_uri":...}` (only these two fields; all others
/// `omitempty`) followed by the trailing newline `json.Encoder.Encode` writes.
/// `jwks_uri` is `issuer + "/jwks"` (`op.go:80`).
pub fn discovery_configuration_json(issuer: &str) -> String {
    #[derive(serde::Serialize)]
    struct DiscoveryConfiguration<'a> {
        issuer: &'a str,
        jwks_uri: &'a str,
    }

    let mut body = serde_json::to_string(&DiscoveryConfiguration {
        issuer,
        jwks_uri: &format!("{issuer}{JWKS_PATH}"),
    })
    .expect("discovery configuration serialization cannot fail");
    body.push('\n');
    body
}

async fn openid_configuration(State(signer): State<Arc<Signer>>) -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "application/json")],
        discovery_configuration_json(signer.issuer()),
    )
}

async fn jwks(State(signer): State<Arc<Signer>>) -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "application/json")],
        signer.jwks_document(),
    )
}

/// Generates the fresh self-signed `localhost` certificate the discovery
/// listener presents, mirroring `NewSelfSignedCertificate("jumpstarter oidc",
/// ["localhost"], [])` (`cmd/main.go:206`, `selfsigned.go:14-40`): serial 1,
/// subject CN only, valid now..now+365d, explicit basic-constraints CA:FALSE,
/// a single `localhost` DNS SAN. Key type diverges from Go's RSA-2048 (rcgen
/// emits ECDSA P-256); the self-signed material has no interop contract on key
/// type, so this is a benign, documented divergence (same as the gRPC
/// listeners' self-signed fallback).
fn self_signed_localhost_material(
) -> Result<(CertificateDer<'static>, PrivateKeyDer<'static>), DiscoveryError> {
    use rcgen::{
        CertificateParams, DistinguishedName, DnType, Ia5String, IsCa, KeyPair, SanType,
        SerialNumber,
    };

    let mut params = CertificateParams::default();
    params.serial_number = Some(SerialNumber::from_slice(&[1]));
    let mut distinguished_name = DistinguishedName::new();
    distinguished_name.push(DnType::CommonName, OIDC_COMMON_NAME);
    params.distinguished_name = distinguished_name;
    let not_before = OffsetDateTime::now_utc();
    params.not_before = not_before;
    params.not_after = not_before + Duration::days(365);
    params.is_ca = IsCa::ExplicitNoCa;
    params.subject_alt_names = vec![SanType::DnsName(Ia5String::try_from("localhost")?)];

    let key_pair = KeyPair::generate()?;
    let certificate = params.self_signed(&key_pair)?;

    Ok((
        certificate.der().clone(),
        PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key_pair.serialize_der())),
    ))
}

/// Builds the rustls `ServerConfig` (no client auth) for the discovery
/// listener, presenting a fresh self-signed `localhost` certificate and
/// advertising `h2` then `http/1.1` via ALPN. Uses the process-default rustls
/// `CryptoProvider` (the binaries install ring at startup).
pub fn self_signed_localhost() -> Result<Arc<ServerConfig>, DiscoveryError> {
    let (cert, key) = self_signed_localhost_material()?;
    let mut config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(vec![cert], key)?;
    config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    Ok(Arc::new(config))
}

/// Serves the discovery [`router`] over TLS on an already-bound listener until
/// the listener errors. Each accepted connection is handled on its own task;
/// a per-connection TLS handshake or HTTP error is logged and dropped (it does
/// not tear the server down), matching the debug-surface intent.
pub async fn serve(
    listener: TcpListener,
    tls: Arc<ServerConfig>,
    router: Router,
) -> Result<(), DiscoveryError> {
    let acceptor = TlsAcceptor::from(tls);
    loop {
        let (stream, peer) = listener.accept().await.map_err(DiscoveryError::Accept)?;
        let acceptor = acceptor.clone();
        let service = TowerToHyperService::new(router.clone());
        tokio::spawn(async move {
            let tls_stream = match acceptor.accept(stream).await {
                Ok(stream) => stream,
                Err(err) => {
                    tracing::debug!(%peer, error = %err, "discovery TLS handshake failed");
                    return;
                }
            };
            if let Err(err) = ConnBuilder::new(TokioExecutor::new())
                .serve_connection(TokioIo::new(tls_stream), service)
                .await
            {
                tracing::debug!(%peer, error = %err, "discovery connection error");
            }
        });
    }
}

/// Binds `DISCOVERY_ADDR`, generates the fresh self-signed `localhost`
/// certificate, and serves the discovery routes for `signer` — the production
/// entry point (`cmd/main.go:293-299`, all replicas).
pub async fn serve_default(signer: Arc<Signer>) -> Result<(), DiscoveryError> {
    let listener =
        TcpListener::bind(DISCOVERY_ADDR)
            .await
            .map_err(|source| DiscoveryError::Bind {
                addr: DISCOVERY_ADDR.to_string(),
                source,
            })?;
    let tls = self_signed_localhost()?;
    tracing::info!(addr = DISCOVERY_ADDR, "serving internal OIDC discovery");
    serve(listener, tls, router(signer)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::signer::{INTERNAL_AUDIENCE, INTERNAL_ISSUER};

    fn install_ring() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }

    fn test_signer() -> Arc<Signer> {
        Arc::new(
            Signer::from_seed(b"golden-controller-key", INTERNAL_ISSUER, INTERNAL_AUDIENCE)
                .expect("signer from seed"),
        )
    }

    /// The discovery document is exactly the two Go-emitted fields, in order,
    /// with the trailing newline `json.Encoder.Encode` writes; `jwks_uri` is
    /// derived from the issuer (`op.go:80`).
    #[test]
    fn discovery_document_matches_go_shape() {
        assert_eq!(
            discovery_configuration_json(INTERNAL_ISSUER),
            format!(
                "{{\"issuer\":\"{INTERNAL_ISSUER}\",\"jwks_uri\":\"{INTERNAL_ISSUER}/jwks\"}}\n"
            )
        );
        // Non-default issuer: jwks_uri tracks it.
        assert_eq!(
            discovery_configuration_json("https://issuer.example:9000"),
            "{\"issuer\":\"https://issuer.example:9000\",\"jwks_uri\":\"https://issuer.example:9000/jwks\"}\n"
        );
    }

    /// Pins the certificate SAN without an x509 parser: a rustls client that
    /// trusts the generated cert as its sole root completes the handshake for
    /// server name `localhost` (proving `localhost` is in the SAN) and rejects
    /// a non-`localhost` name (proving the SAN is not a wildcard). This is the
    /// property the cert exists for — the CN/serial/validity details are
    /// covered by the shared self-signed path in jumpstarter-controller-runtime.
    #[tokio::test]
    async fn served_cert_validates_for_localhost() {
        use rustls::pki_types::ServerName;
        use rustls::{ClientConfig, RootCertStore, ServerConfig};
        use tokio::net::TcpStream;
        use tokio_rustls::TlsConnector;

        install_ring();
        let (cert, key) = self_signed_localhost_material().unwrap();

        // Server presenting exactly the generated certificate.
        let server_config = Arc::new(
            ServerConfig::builder()
                .with_no_client_auth()
                .with_single_cert(vec![cert.clone()], key)
                .unwrap(),
        );
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let acceptor = TlsAcceptor::from(server_config);
        let accept_task = tokio::spawn(async move {
            // Two handshakes: the localhost success and the mismatch attempt.
            for _ in 0..2 {
                if let Ok((stream, _)) = listener.accept().await {
                    let acceptor = acceptor.clone();
                    tokio::spawn(async move {
                        let _ = acceptor.accept(stream).await;
                    });
                }
            }
        });

        // Client trusting only the generated cert.
        let mut roots = RootCertStore::empty();
        roots.add(cert).unwrap();
        let connector = TlsConnector::from(Arc::new(
            ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth(),
        ));

        // localhost is in the SAN -> handshake succeeds.
        let stream = TcpStream::connect(addr).await.unwrap();
        connector
            .connect(ServerName::try_from("localhost").unwrap(), stream)
            .await
            .expect("localhost must match the certificate SAN");

        // A different server name is not covered -> handshake fails.
        let stream = TcpStream::connect(addr).await.unwrap();
        assert!(
            connector
                .connect(
                    ServerName::try_from("not-localhost.example").unwrap(),
                    stream
                )
                .await
                .is_err(),
            "a non-localhost server name must be rejected by the SAN check"
        );

        accept_task.abort();
    }

    /// Two startups produce distinct certificates (fresh keypair each time),
    /// mirroring Go generating a new self-signed cert per process.
    #[test]
    fn self_signed_cert_is_fresh_each_call() {
        let (a, _) = self_signed_localhost_material().unwrap();
        let (b, _) = self_signed_localhost_material().unwrap();
        assert_ne!(a, b);
    }

    /// End-to-end: both endpoints are served over TLS on the self-signed
    /// listener, a client that accepts the self-signed cert gets the exact
    /// discovery document and a JWKS whose single key round-trips a token the
    /// signer minted (what an external K8s authenticator would do).
    #[tokio::test]
    async fn endpoints_served_over_tls() {
        install_ring();

        let signer = test_signer();
        let expected_jwks = signer.jwks_document();
        let token = signer.token("client:default:sample:uid-1").unwrap();

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let tls = self_signed_localhost().unwrap();
        let server = tokio::spawn(serve(listener, tls, router(signer.clone())));

        // Accepts the self-signed cert (debug server): the observable contract
        // is the served bytes, not chain trust.
        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .build()
            .unwrap();

        // /.well-known/openid-configuration
        let resp = client
            .get(format!("https://{addr}{OPENID_CONFIGURATION_PATH}"))
            .send()
            .await
            .unwrap();
        assert!(resp.status().is_success());
        assert_eq!(
            resp.headers()
                .get(header::CONTENT_TYPE)
                .map(|v| v.to_str().unwrap().to_string()),
            Some("application/json".to_string())
        );
        let body = resp.text().await.unwrap();
        assert_eq!(body, discovery_configuration_json(INTERNAL_ISSUER));
        let doc: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(doc["issuer"], INTERNAL_ISSUER);
        assert_eq!(doc["jwks_uri"], format!("{INTERNAL_ISSUER}/jwks"));

        // /jwks — byte-identical to the signer's document.
        let resp = client
            .get(format!("https://{addr}{JWKS_PATH}"))
            .send()
            .await
            .unwrap();
        assert!(resp.status().is_success());
        let jwks_body = resp.text().await.unwrap();
        assert_eq!(jwks_body, expected_jwks);

        // The served JWKS verifies a signer-minted token (external-verifier POV).
        let jwks: serde_json::Value = serde_json::from_str(&jwks_body).unwrap();
        let key = &jwks["keys"][0];
        let decoding = jsonwebtoken::DecodingKey::from_ec_components(
            key["x"].as_str().unwrap(),
            key["y"].as_str().unwrap(),
        )
        .unwrap();
        let mut validation = jsonwebtoken::Validation::new(jsonwebtoken::Algorithm::ES256);
        validation.set_issuer(&[INTERNAL_ISSUER]);
        validation.set_audience(&[INTERNAL_AUDIENCE]);
        jsonwebtoken::decode::<serde_json::Value>(&token, &decoding, &validation)
            .expect("served JWKS verifies a signer-minted token");

        server.abort();
    }

    /// An unknown route is a plain 404 (axum default) — no panic, server keeps
    /// running for the other endpoints.
    #[tokio::test]
    async fn unknown_route_is_404() {
        install_ring();

        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let tls = self_signed_localhost().unwrap();
        let server = tokio::spawn(serve(listener, tls, router(test_signer())));

        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true)
            .build()
            .unwrap();
        let resp = client
            .get(format!("https://{addr}/nope"))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), reqwest::StatusCode::NOT_FOUND);

        server.abort();
    }
}
