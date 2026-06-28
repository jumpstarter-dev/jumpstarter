//! Insecure (skip-verify) TLS — connect over TLS but accept any server
//! certificate, matching Python's `ssl_channel_credentials` insecure path
//! (`common/grpc.py`: `check_hostname=False`, `verify_mode=CERT_NONE`). This is
//! "encrypted but unverified", *not* plaintext (spec doc 07 §8.3).
//!
//! tonic's `ClientTlsConfig` has no skip-verify option, so we build a custom rustls
//! `ClientConfig` with a no-op certificate verifier and feed it to tonic through a
//! `connect_with_connector` connector.

use std::sync::Arc;
use std::time::Duration;

use hyper_util::rt::TokioIo;
use tokio::net::TcpStream;
use tokio_rustls::rustls::client::danger::{
    HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier,
};
use tokio_rustls::rustls::crypto::{
    ring, verify_tls12_signature, verify_tls13_signature, CryptoProvider,
};
use tokio_rustls::rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use tokio_rustls::rustls::{
    ClientConfig, DigitallySignedStruct, Error as RustlsError, SignatureScheme,
};
use tokio_rustls::TlsConnector;
use tonic::transport::{Channel, Endpoint};

use crate::error::ClientError;

/// A verifier that accepts any certificate chain (but still validates the
/// handshake signatures, so the connection is genuinely encrypted).
#[derive(Debug)]
struct NoVerify(Arc<CryptoProvider>);

impl ServerCertVerifier for NoVerify {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, RustlsError> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls12_signature(
            message,
            cert,
            dss,
            &self.0.signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls13_signature(
            message,
            cert,
            dss,
            &self.0.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

fn insecure_config() -> ClientConfig {
    let provider = Arc::new(ring::default_provider());
    let mut config = ClientConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()
        .expect("ring provider supports the default protocol versions")
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify(provider)))
        .with_no_client_auth();
    // gRPC runs over HTTP/2; advertise it via ALPN.
    config.alpn_protocols = vec![b"h2".to_vec()];
    config
}

fn io_error(msg: &'static str) -> std::io::Error {
    std::io::Error::other(msg)
}

/// Connect a `Channel` to `target` (`host:port`) over TLS without verifying the
/// server certificate.
pub async fn connect(target: &str) -> Result<Channel, ClientError> {
    let tls = Arc::new(insecure_config());

    let connector = tower::service_fn(move |uri: http::Uri| {
        let tls = tls.clone();
        async move {
            let host = uri
                .host()
                .ok_or_else(|| io_error("missing host"))?
                .to_string();
            let port = uri.port_u16().unwrap_or(443);
            let tcp = TcpStream::connect((host.as_str(), port)).await?;
            let server_name =
                ServerName::try_from(host).map_err(|_| io_error("invalid dns name"))?;
            let stream = TlsConnector::from(tls).connect(server_name, tcp).await?;
            Ok::<_, std::io::Error>(TokioIo::new(stream))
        }
    });

    // Use an `http` scheme: our connector supplies the TLS stream, so tonic must
    // not try to add (or require) its own TLS. An `https` URI without `tls_config`
    // is rejected as `HttpsUriWithoutTlsSupport`.
    let endpoint = Endpoint::from_shared(format!("http://{target}"))
        .map_err(|e| ClientError::Config(format!("invalid endpoint {target}: {e}")))?
        .http2_keep_alive_interval(Duration::from_secs(20))
        .keep_alive_timeout(Duration::from_secs(180))
        .keep_alive_while_idle(true)
        // Large HTTP/2 windows so bulk resource/flash transfers aren't window-gated.
        .initial_stream_window_size(8 * 1024 * 1024)
        .initial_connection_window_size(16 * 1024 * 1024);

    // Connect on the multi-threaded IO runtime (see `crate::io_runtime`).
    crate::io_runtime()
        .spawn(async move { endpoint.connect_with_connector(connector).await })
        .await
        .map_err(|e| ClientError::Config(format!("connect task panicked: {e}")))?
        .map_err(Into::into)
}
