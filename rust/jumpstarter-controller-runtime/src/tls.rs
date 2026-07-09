//! Server TLS material resolution for the gRPC listeners, mirroring
//! `controller/internal/service/{selfsigned.go,endpoints.go}` and the TLS
//! sections of `ControllerService.Start` (`controller_service.go:1092-1171`)
//! and `RouterService.Start` (`router_service.go:117-157`).
//!
//! Precedence (identical in both Go services):
//! 1. If **both** `EXTERNAL_CERT_PEM` and `EXTERNAL_KEY_PEM` are set to
//!    non-empty values, they are treated as **file paths** whose contents are
//!    PEM (despite the `_PEM` suffix — the Go comment claims the variables
//!    "contain the PEM-encoded certificate" but the code `os.ReadFile`s them;
//!    the operator sets them to `/tls/tls.crt` / `/tls/tls.key` from a
//!    mounted Secret, `jumpstarter_controller.go:764-765`).
//! 2. Otherwise a self-signed certificate is generated with
//!    CN `"jumpstarter controller"` / `"jumpstarter router"` and SANs derived
//!    from `GRPC_ENDPOINT` / `GRPC_ROUTER_ENDPOINT` via `endpointToSAN`
//!    (host part only; an IP literal becomes an IP SAN, anything else a DNS
//!    SAN).
//!
//! `CA_BUNDLE_PEM` is different: it carries PEM **content** (the operator
//! wires it from the CA ConfigMap's `ca.crt` key,
//! `jumpstarter_controller.go:735-748`) and is only *exposed to clients* by
//! the login service (`login/service.go:98`); it plays no part in server
//! certificate selection.
//!
//! Divergence note: Go generates RSA-2048 self-signed keys
//! (`selfsigned.go:26`); rcgen generates ECDSA P-256. The self-signed
//! fallback has no interop contract on key type (clients either trust the
//! cert insecurely or replace it with external material), so this is a
//! documented, benign divergence.
//!
//! This module never installs a rustls `CryptoProvider`; the binaries install
//! the ring provider at startup before building any `ServerConfig`.

use std::net::IpAddr;
use std::path::{Path, PathBuf};

use rcgen::{
    CertificateParams, DistinguishedName, DnType, Ia5String, IsCa, KeyPair, SanType, SerialNumber,
};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use thiserror::Error;
use time::{Duration, OffsetDateTime};

/// Env var holding the *path* to the PEM server certificate (operator:
/// `/tls/tls.crt`).
pub const ENV_EXTERNAL_CERT_PEM: &str = "EXTERNAL_CERT_PEM";
/// Env var holding the *path* to the PEM server private key (operator:
/// `/tls/tls.key`).
pub const ENV_EXTERNAL_KEY_PEM: &str = "EXTERNAL_KEY_PEM";
/// Env var holding PEM *content* of the CA bundle advertised to clients by
/// the login service.
pub const ENV_CA_BUNDLE_PEM: &str = "CA_BUNDLE_PEM";
/// Env var holding the advertised controller gRPC endpoint (`host:port`).
pub const ENV_GRPC_ENDPOINT: &str = "GRPC_ENDPOINT";
/// Env var holding the advertised router gRPC endpoint (`host:port`).
pub const ENV_GRPC_ROUTER_ENDPOINT: &str = "GRPC_ROUTER_ENDPOINT";

/// Self-signed CN used by the controller listener (`controller_service.go:1122`).
pub const CONTROLLER_COMMON_NAME: &str = "jumpstarter controller";
/// Self-signed CN used by the router listener (`router_service.go:147`).
pub const ROUTER_COMMON_NAME: &str = "jumpstarter router";
/// Self-signed CN used by the internal OIDC provider (`cmd/main.go:206`,
/// always `localhost`-only, never external material).
pub const OIDC_COMMON_NAME: &str = "jumpstarter oidc";

/// Default advertised controller endpoint (`endpoints.go:8-14`).
pub const DEFAULT_CONTROLLER_ENDPOINT: &str = "localhost:8082";
/// Default advertised router endpoint (`endpoints.go:16-22`).
pub const DEFAULT_ROUTER_ENDPOINT: &str = "localhost:8083";

/// ALPN protocols of the Go controller listener, order preserved
/// (`controller_service.go:1170`: `NextProtos: []string{"http/1.1", "h2"}`).
/// gRPC clients offer only `h2`; the http/1.1-first preference existed for
/// the (dead) grpc-gateway mux sharing the port.
pub fn controller_alpn() -> Vec<Vec<u8>> {
    vec![b"http/1.1".to_vec(), b"h2".to_vec()]
}

/// ALPN protocols of the Go router listener: grpc-go's
/// `credentials.NewServerTLSFromCert` advertises `h2` only
/// (`router_service.go:154`).
pub fn router_alpn() -> Vec<Vec<u8>> {
    vec![b"h2".to_vec()]
}

/// Errors resolving server TLS material. All are fatal at startup in Go
/// (`Start` returns the error and the manager exits).
#[derive(Debug, Error)]
pub enum TlsError {
    /// The advertised endpoint is not a valid `host:port`. Message mirrors
    /// Go's `net.AddrError` (`"address " + addr + ": " + why`) as produced by
    /// `net.SplitHostPort` inside `endpointToSAN` (`endpoints.go:24-28`).
    #[error("address {address}: {reason}")]
    InvalidEndpoint {
        address: String,
        reason: &'static str,
    },
    /// `controller_service.go:1108-1111`.
    #[error("failed to read external certificate file: {0}")]
    ReadExternalCertificate(#[source] std::io::Error),
    /// `controller_service.go:1112-1115`.
    #[error("failed to read external key file: {0}")]
    ReadExternalKey(#[source] std::io::Error),
    /// `controller_service.go:1116-1119` (`tls.X509KeyPair` failure).
    #[error("failed to parse external certificate: {reason}")]
    ParseExternalCertificate { reason: String },
    /// rcgen failure generating the self-signed fallback.
    #[error("failed to generate self-signed certificate: {0}")]
    SelfSigned(#[from] rcgen::Error),
    /// rustls rejected the certificate/key when building the server config
    /// (e.g. key does not match the certificate).
    #[error("failed to build rustls server config: {0}")]
    ServerConfig(#[from] rustls::Error),
}

/// Advertised controller endpoint: `GRPC_ENDPOINT` or `localhost:8082`
/// (`endpoints.go:8-14`).
pub fn controller_endpoint() -> String {
    env_or_default(ENV_GRPC_ENDPOINT, DEFAULT_CONTROLLER_ENDPOINT)
}

/// Advertised router endpoint: `GRPC_ROUTER_ENDPOINT` or `localhost:8083`
/// (`endpoints.go:16-22`).
pub fn router_endpoint() -> String {
    env_or_default(ENV_GRPC_ROUTER_ENDPOINT, DEFAULT_ROUTER_ENDPOINT)
}

/// CA bundle PEM *content* for clients, from `CA_BUNDLE_PEM`
/// (`login/service.go:98`). Go passes the raw `os.Getenv` result through and
/// treats empty as "no bundle"; `None` here covers both unset and empty.
pub fn ca_bundle_pem() -> Option<String> {
    std::env::var(ENV_CA_BUNDLE_PEM)
        .ok()
        .filter(|value| !value.is_empty())
}

fn env_or_default(name: &str, default: &str) -> String {
    match std::env::var(name) {
        Ok(value) if !value.is_empty() => value,
        _ => default.to_string(),
    }
}

/// External certificate/key *paths* from the environment. `Some` only when
/// **both** `EXTERNAL_CERT_PEM` and `EXTERNAL_KEY_PEM` are non-empty,
/// mirroring `if certPEMPath != "" && keyPEMPath != ""`
/// (`controller_service.go:1107`): a single set variable still falls back to
/// self-signed.
pub fn external_cert_paths_from_env() -> Option<(PathBuf, PathBuf)> {
    let cert = std::env::var(ENV_EXTERNAL_CERT_PEM)
        .ok()
        .filter(|value| !value.is_empty())?;
    let key = std::env::var(ENV_EXTERNAL_KEY_PEM)
        .ok()
        .filter(|value| !value.is_empty())?;
    Some((cert.into(), key.into()))
}

/// Port of `endpointToSAN` (`endpoints.go:24-35`): split `host:port`, then
/// classify the host — an IP literal becomes the sole IP SAN, anything else
/// the sole DNS SAN.
pub fn endpoint_to_san(endpoint: &str) -> Result<(Vec<String>, Vec<IpAddr>), TlsError> {
    let host = split_host_port(endpoint)?;
    match host.parse::<IpAddr>() {
        Ok(ip) => Ok((Vec::new(), vec![ip])),
        Err(_) => Ok((vec![host.to_string()], Vec::new())),
    }
}

/// Faithful port of Go `net.SplitHostPort` (host half only), including its
/// error strings, so a malformed `GRPC_ENDPOINT` fails identically.
fn split_host_port(hostport: &str) -> Result<&str, TlsError> {
    const MISSING_PORT: &str = "missing port in address";
    const TOO_MANY_COLONS: &str = "too many colons in address";

    let err = |reason: &'static str| TlsError::InvalidEndpoint {
        address: hostport.to_string(),
        reason,
    };

    let bytes = hostport.as_bytes();
    // The port starts after the last colon.
    let Some(last_colon) = bytes.iter().rposition(|&b| b == b':') else {
        return Err(err(MISSING_PORT));
    };

    let (host, host_search_from) = if bytes.first() == Some(&b'[') {
        // Expect the first ']' just before the last ':'.
        let Some(end) = bytes.iter().position(|&b| b == b']') else {
            return Err(err("missing ']' in address"));
        };
        if end + 1 == bytes.len() {
            // There can't be a ':' behind the ']' now.
            return Err(err(MISSING_PORT));
        }
        if end + 1 != last_colon {
            // Either ']' isn't followed by a colon, or it is followed by a
            // colon that is not the last one.
            if bytes[end + 1] == b':' {
                return Err(err(TOO_MANY_COLONS));
            }
            return Err(err(MISSING_PORT));
        }
        (&hostport[1..end], end + 1)
    } else {
        let host = &hostport[..last_colon];
        if host.contains(':') {
            return Err(err(TOO_MANY_COLONS));
        }
        (host, 0)
    };

    if bytes[usize::from(host_search_from > 0)..].contains(&b'[') {
        return Err(err("unexpected '[' in address"));
    }
    if bytes[host_search_from..].contains(&b']') {
        return Err(err("unexpected ']' in address"));
    }

    Ok(host)
}

/// Resolved server certificate chain + private key, convertible into rustls
/// `ServerConfig`s for the tonic (gRPC) and axum (HTTP) listeners.
#[derive(Debug)]
pub struct TlsMaterial {
    /// DER certificate chain, leaf first.
    pub cert_chain: Vec<CertificateDer<'static>>,
    /// DER private key.
    pub key: PrivateKeyDer<'static>,
}

impl TlsMaterial {
    /// Port of `NewSelfSignedCertificate` (`selfsigned.go:14-40`): serial 1,
    /// subject/issuer CN only, valid from now for 365 days,
    /// `BasicConstraintsValid: true` with `IsCA: false` (explicit CA:FALSE),
    /// no key-usage extensions, the given DNS/IP SANs. Key type diverges
    /// (ECDSA P-256 instead of RSA-2048, see module docs).
    pub fn self_signed(
        common_name: &str,
        dns_names: &[String],
        ip_addresses: &[IpAddr],
    ) -> Result<Self, TlsError> {
        let mut params = CertificateParams::default();
        // Go: SerialNumber: big.NewInt(1) — a single 0x01 INTEGER byte.
        params.serial_number = Some(SerialNumber::from_slice(&[1]));
        let mut distinguished_name = DistinguishedName::new();
        distinguished_name.push(DnType::CommonName, common_name);
        params.distinguished_name = distinguished_name;
        let not_before = OffsetDateTime::now_utc();
        params.not_before = not_before;
        params.not_after = not_before + Duration::days(365);
        // Go: BasicConstraintsValid: true (with IsCA left false) writes an
        // explicit basicConstraints CA:FALSE extension.
        params.is_ca = IsCa::ExplicitNoCa;
        let mut subject_alt_names = Vec::with_capacity(dns_names.len() + ip_addresses.len());
        for name in dns_names {
            subject_alt_names.push(SanType::DnsName(Ia5String::try_from(name.as_str())?));
        }
        for ip in ip_addresses {
            subject_alt_names.push(SanType::IpAddress(*ip));
        }
        params.subject_alt_names = subject_alt_names;

        let key_pair = KeyPair::generate()?;
        let certificate = params.self_signed(&key_pair)?;

        Ok(Self {
            cert_chain: vec![certificate.der().clone()],
            key: PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key_pair.serialize_der())),
        })
    }

    /// Parses PEM certificate-chain and key bytes, the rustls counterpart of
    /// `tls.X509KeyPair` (all CERTIFICATE blocks form the chain; the key may
    /// be PKCS#1, SEC1 or PKCS#8).
    pub fn from_pem(cert_pem: &[u8], key_pem: &[u8]) -> Result<Self, TlsError> {
        let parse_err = |reason: String| TlsError::ParseExternalCertificate { reason };

        let cert_chain = rustls_pemfile::certs(&mut &*cert_pem)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|err| parse_err(err.to_string()))?;
        if cert_chain.is_empty() {
            return Err(parse_err("no certificate found in PEM input".to_string()));
        }

        let key = rustls_pemfile::private_key(&mut &*key_pem)
            .map_err(|err| parse_err(err.to_string()))?
            .ok_or_else(|| parse_err("no private key found in PEM input".to_string()))?;

        Ok(Self { cert_chain, key })
    }

    /// Reads and parses the external certificate/key files
    /// (`controller_service.go:1108-1120`).
    pub async fn from_files(cert_path: &Path, key_path: &Path) -> Result<Self, TlsError> {
        let cert_pem = tokio::fs::read(cert_path)
            .await
            .map_err(TlsError::ReadExternalCertificate)?;
        let key_pem = tokio::fs::read(key_path)
            .await
            .map_err(TlsError::ReadExternalKey)?;
        Self::from_pem(&cert_pem, &key_pem)
    }

    /// Builds a rustls `ServerConfig` (no client auth) with the given ALPN
    /// protocols — [`controller_alpn`] for the shared gRPC/HTTP listener,
    /// [`router_alpn`] for the router. Usable by tonic (via a tokio-rustls
    /// acceptor) and axum alike.
    ///
    /// Uses the process-default rustls `CryptoProvider`; the caller is
    /// responsible for installing the ring provider beforehand.
    pub fn into_server_config(
        self,
        alpn_protocols: Vec<Vec<u8>>,
    ) -> Result<rustls::ServerConfig, TlsError> {
        let mut config = rustls::ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(self.cert_chain, self.key)?;
        config.alpn_protocols = alpn_protocols;
        Ok(config)
    }
}

/// Resolves the server TLS material with the exact Go precedence, reading
/// `EXTERNAL_CERT_PEM`/`EXTERNAL_KEY_PEM` from the environment. See
/// [`resolve_server_tls_with`] for the env-free core.
pub async fn resolve_server_tls(
    common_name: &str,
    endpoint: &str,
) -> Result<TlsMaterial, TlsError> {
    resolve_server_tls_with(external_cert_paths_from_env(), common_name, endpoint).await
}

/// Env-free core of [`resolve_server_tls`].
///
/// Note the Go quirk preserved here: `endpointToSAN` runs *before* the
/// external-certificate check (`controller_service.go:1095-1098`), so a
/// malformed endpoint is fatal even when external material would make the
/// SANs irrelevant.
pub async fn resolve_server_tls_with(
    external: Option<(PathBuf, PathBuf)>,
    common_name: &str,
    endpoint: &str,
) -> Result<TlsMaterial, TlsError> {
    let (dns_names, ip_addresses) = endpoint_to_san(endpoint)?;
    match external {
        Some((cert_path, key_path)) => {
            tracing::debug!(
                cert_path = %cert_path.display(),
                key_path = %key_path.display(),
                "loading external server certificate"
            );
            TlsMaterial::from_files(&cert_path, &key_path).await
        }
        None => {
            tracing::debug!(
                common_name,
                ?dns_names,
                ?ip_addresses,
                "generating self-signed server certificate"
            );
            TlsMaterial::self_signed(common_name, &dns_names, &ip_addresses)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rcgen::DnValue;
    use std::net::{Ipv4Addr, Ipv6Addr};

    fn install_ring() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }

    /// Round-trips generated material through rcgen's x509 parser.
    fn reparse(material: &TlsMaterial) -> CertificateParams {
        CertificateParams::from_ca_cert_der(&material.cert_chain[0]).unwrap()
    }

    fn common_name(params: &CertificateParams) -> String {
        match params.distinguished_name.get(&DnType::CommonName) {
            Some(DnValue::Utf8String(name)) => name.clone(),
            Some(DnValue::PrintableString(name)) => name.as_str().to_string(),
            other => panic!("unexpected CN value: {other:?}"),
        }
    }

    #[test]
    fn endpoint_to_san_dns_host() {
        // endpoints.go:29-34: non-IP host => single DNS SAN, no IP SANs.
        let (dns, ips) = endpoint_to_san("grpc.jumpstarter.example.com:8082").unwrap();
        assert_eq!(dns, vec!["grpc.jumpstarter.example.com".to_string()]);
        assert!(ips.is_empty());
    }

    #[test]
    fn endpoint_to_san_ipv4_host() {
        let (dns, ips) = endpoint_to_san("192.0.2.7:8082").unwrap();
        assert!(dns.is_empty());
        assert_eq!(ips, vec![IpAddr::V4(Ipv4Addr::new(192, 0, 2, 7))]);
    }

    #[test]
    fn endpoint_to_san_bracketed_ipv6_host() {
        let (dns, ips) = endpoint_to_san("[2001:db8::1]:8082").unwrap();
        assert!(dns.is_empty());
        assert_eq!(
            ips,
            vec![IpAddr::V6("2001:db8::1".parse::<Ipv6Addr>().unwrap())]
        );
    }

    #[test]
    fn endpoint_to_san_error_taxonomy_matches_go_split_host_port() {
        // Missing port: Go net.SplitHostPort AddrError text.
        assert_eq!(
            endpoint_to_san("localhost").unwrap_err().to_string(),
            "address localhost: missing port in address"
        );
        // Unbracketed IPv6 splits at the last colon and trips the host check.
        assert_eq!(
            endpoint_to_san("2001:db8::1:8082").unwrap_err().to_string(),
            "address 2001:db8::1:8082: too many colons in address"
        );
        // Bracket errors.
        assert_eq!(
            endpoint_to_san("[2001:db8::1:8082")
                .unwrap_err()
                .to_string(),
            "address [2001:db8::1:8082: missing ']' in address"
        );
        assert_eq!(
            endpoint_to_san("[2001:db8::1]").unwrap_err().to_string(),
            "address [2001:db8::1]: missing port in address"
        );
        assert_eq!(
            endpoint_to_san("[2001:db8::1]:8082:9090")
                .unwrap_err()
                .to_string(),
            "address [2001:db8::1]:8082:9090: too many colons in address"
        );
    }

    #[test]
    fn self_signed_dns_san_for_sample_grpc_endpoint() {
        // Sample GRPC_ENDPOINT resolution -> SAN construction, end to end.
        let (dns, ips) = endpoint_to_san("grpc.jumpstarter.example.com:443").unwrap();
        let material = TlsMaterial::self_signed(CONTROLLER_COMMON_NAME, &dns, &ips).unwrap();
        let params = reparse(&material);

        assert_eq!(common_name(&params), CONTROLLER_COMMON_NAME);
        assert_eq!(
            params.subject_alt_names,
            vec![SanType::DnsName(
                Ia5String::try_from("grpc.jumpstarter.example.com").unwrap()
            )]
        );
        // Go: big.NewInt(1).
        assert_eq!(params.serial_number.as_ref().unwrap().to_bytes(), vec![1u8]);
        // Go: BasicConstraintsValid true + IsCA false => explicit CA:FALSE.
        assert_eq!(params.is_ca, IsCa::ExplicitNoCa);
        // Go: NotBefore now, NotAfter now + 365 days (both truncate to
        // seconds identically in the DER encoding).
        assert_eq!(params.not_after - params.not_before, Duration::days(365));
        // Go template sets no key usages.
        assert!(params.key_usages.is_empty());
        assert!(params.extended_key_usages.is_empty());
    }

    #[test]
    fn self_signed_ip_san_for_ip_endpoint() {
        let (dns, ips) = endpoint_to_san("192.0.2.10:8083").unwrap();
        let material = TlsMaterial::self_signed(ROUTER_COMMON_NAME, &dns, &ips).unwrap();
        let params = reparse(&material);

        assert_eq!(common_name(&params), ROUTER_COMMON_NAME);
        assert_eq!(
            params.subject_alt_names,
            vec![SanType::IpAddress(IpAddr::V4(Ipv4Addr::new(192, 0, 2, 10)))]
        );
    }

    #[test]
    fn self_signed_ipv6_san() {
        let (dns, ips) = endpoint_to_san("[2001:db8::1]:8082").unwrap();
        let material = TlsMaterial::self_signed(CONTROLLER_COMMON_NAME, &dns, &ips).unwrap();
        let params = reparse(&material);
        assert_eq!(
            params.subject_alt_names,
            vec![SanType::IpAddress(IpAddr::V6(
                "2001:db8::1".parse::<Ipv6Addr>().unwrap()
            ))]
        );
    }

    #[test]
    fn oidc_style_localhost_dns_only() {
        // cmd/main.go:206: CN "jumpstarter oidc", DNS ["localhost"], no IPs.
        let material =
            TlsMaterial::self_signed(OIDC_COMMON_NAME, &["localhost".to_string()], &[]).unwrap();
        let params = reparse(&material);
        assert_eq!(common_name(&params), OIDC_COMMON_NAME);
        assert_eq!(
            params.subject_alt_names,
            vec![SanType::DnsName(Ia5String::try_from("localhost").unwrap())]
        );
    }

    #[test]
    fn from_pem_round_trip_and_server_config() {
        install_ring();

        // Generate a keypair+cert with rcgen, serialize to PEM, feed it back
        // through the external-material path.
        let mut params = CertificateParams::default();
        params.subject_alt_names = vec![SanType::DnsName(
            Ia5String::try_from("external.example.com").unwrap(),
        )];
        let key_pair = KeyPair::generate().unwrap();
        let cert = params.self_signed(&key_pair).unwrap();

        let material =
            TlsMaterial::from_pem(cert.pem().as_bytes(), key_pair.serialize_pem().as_bytes())
                .unwrap();
        assert_eq!(material.cert_chain.len(), 1);
        assert_eq!(&material.cert_chain[0], cert.der());

        let config = material.into_server_config(controller_alpn()).unwrap();
        assert_eq!(
            config.alpn_protocols,
            vec![b"http/1.1".to_vec(), b"h2".to_vec()]
        );
    }

    #[test]
    fn from_pem_error_taxonomy() {
        let err = TlsMaterial::from_pem(b"not pem at all", b"also not pem").unwrap_err();
        assert!(matches!(err, TlsError::ParseExternalCertificate { .. }));
        assert!(err
            .to_string()
            .starts_with("failed to parse external certificate: "));

        // Valid cert but missing key block.
        let params = CertificateParams::default();
        let key_pair = KeyPair::generate().unwrap();
        let cert = params.self_signed(&key_pair).unwrap();
        let err = TlsMaterial::from_pem(cert.pem().as_bytes(), b"").unwrap_err();
        assert!(matches!(err, TlsError::ParseExternalCertificate { .. }));
    }

    #[tokio::test]
    async fn resolve_prefers_external_files_when_both_paths_given() {
        let dir = std::env::temp_dir().join(format!(
            "jumpstarter-tls-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();

        let params = CertificateParams::default();
        let key_pair = KeyPair::generate().unwrap();
        let cert = params.self_signed(&key_pair).unwrap();
        let cert_path = dir.join("tls.crt");
        let key_path = dir.join("tls.key");
        std::fs::write(&cert_path, cert.pem()).unwrap();
        std::fs::write(&key_path, key_pair.serialize_pem()).unwrap();

        let material = resolve_server_tls_with(
            Some((cert_path.clone(), key_path.clone())),
            CONTROLLER_COMMON_NAME,
            "grpc.example.com:8082",
        )
        .await
        .unwrap();
        // External material used verbatim: it is the cert we wrote, not a
        // fresh self-signed one.
        assert_eq!(&material.cert_chain[0], cert.der());

        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn resolve_falls_back_to_self_signed_without_external_paths() {
        let material = resolve_server_tls_with(None, ROUTER_COMMON_NAME, "router.example.com:8083")
            .await
            .unwrap();
        let params = reparse(&material);
        assert_eq!(common_name(&params), ROUTER_COMMON_NAME);
        assert_eq!(
            params.subject_alt_names,
            vec![SanType::DnsName(
                Ia5String::try_from("router.example.com").unwrap()
            )]
        );
    }

    #[tokio::test]
    async fn resolve_fails_on_bad_endpoint_even_with_external_material() {
        // Go quirk: endpointToSAN runs before the env check
        // (controller_service.go:1095-1098), so a malformed endpoint is
        // fatal regardless of external material.
        let err = resolve_server_tls_with(
            Some(("/nonexistent/tls.crt".into(), "/nonexistent/tls.key".into())),
            CONTROLLER_COMMON_NAME,
            "no-port-here",
        )
        .await
        .unwrap_err();
        assert!(matches!(err, TlsError::InvalidEndpoint { .. }));
    }

    #[tokio::test]
    async fn resolve_read_error_taxonomy() {
        let err = resolve_server_tls_with(
            Some(("/nonexistent/tls.crt".into(), "/nonexistent/tls.key".into())),
            CONTROLLER_COMMON_NAME,
            "grpc.example.com:8082",
        )
        .await
        .unwrap_err();
        assert!(matches!(err, TlsError::ReadExternalCertificate(_)));
        assert!(err
            .to_string()
            .starts_with("failed to read external certificate file: "));
    }

    #[test]
    fn env_helpers() {
        // Single test for all env-reading helpers to avoid parallel-test
        // races on the process environment.
        std::env::remove_var(ENV_GRPC_ENDPOINT);
        std::env::remove_var(ENV_GRPC_ROUTER_ENDPOINT);
        std::env::remove_var(ENV_CA_BUNDLE_PEM);
        std::env::remove_var(ENV_EXTERNAL_CERT_PEM);
        std::env::remove_var(ENV_EXTERNAL_KEY_PEM);

        // endpoints.go defaults.
        assert_eq!(controller_endpoint(), DEFAULT_CONTROLLER_ENDPOINT);
        assert_eq!(router_endpoint(), DEFAULT_ROUTER_ENDPOINT);
        assert_eq!(ca_bundle_pem(), None);
        assert_eq!(external_cert_paths_from_env(), None);

        std::env::set_var(ENV_GRPC_ENDPOINT, "grpc.example.com:443");
        std::env::set_var(ENV_GRPC_ROUTER_ENDPOINT, "router.example.com:443");
        std::env::set_var(ENV_CA_BUNDLE_PEM, "-----BEGIN CERTIFICATE-----\n...");
        assert_eq!(controller_endpoint(), "grpc.example.com:443");
        assert_eq!(router_endpoint(), "router.example.com:443");
        assert!(ca_bundle_pem().unwrap().starts_with("-----BEGIN"));

        // Only one of the two external vars set => None (Go requires both).
        std::env::set_var(ENV_EXTERNAL_CERT_PEM, "/tls/tls.crt");
        assert_eq!(external_cert_paths_from_env(), None);
        std::env::set_var(ENV_EXTERNAL_KEY_PEM, "/tls/tls.key");
        assert_eq!(
            external_cert_paths_from_env(),
            Some((PathBuf::from("/tls/tls.crt"), PathBuf::from("/tls/tls.key")))
        );

        std::env::remove_var(ENV_GRPC_ENDPOINT);
        std::env::remove_var(ENV_GRPC_ROUTER_ENDPOINT);
        std::env::remove_var(ENV_CA_BUNDLE_PEM);
        std::env::remove_var(ENV_EXTERNAL_CERT_PEM);
        std::env::remove_var(ENV_EXTERNAL_KEY_PEM);
    }
}
