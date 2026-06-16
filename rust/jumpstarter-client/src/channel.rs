//! Building an authenticated TLS channel to the controller
//! (`python/.../common/grpc.py`, `config/grpc.py`).
//!
//! Every controller RPC carries `authorization: Bearer <token>` plus the
//! `jumpstarter-kind` / `jumpstarter-namespace` / `jumpstarter-name` metadata
//! (spec §2.5 / §7 contract item 1). The same TLS path serves both the controller
//! channel and router streams. TLS mode (`is_insecure`): skip-verify when
//! `tls.insecure` or `JUMPSTARTER_GRPC_INSECURE`/`JMP_GRPC_INSECURE` is set (see
//! [`crate::insecure`]), else verify against the config's base64 CA or system roots.

use std::time::Duration;

use base64::Engine as _;
use jumpstarter_config::{ClientConfig, TlsConfig};
use tonic::metadata::MetadataValue;
use tonic::service::interceptor::InterceptedService;
use tonic::service::Interceptor;
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Endpoint};
use tonic::{Request, Status};

use crate::error::ClientError;

/// Whether to skip TLS verification: the config `tls.insecure` flag, or the
/// `JUMPSTARTER_GRPC_INSECURE` / `JMP_GRPC_INSECURE` env vars set to `1`
/// (`common/grpc.py:ssl_channel_credentials`).
pub fn is_insecure(tls: &TlsConfig) -> bool {
    tls.insecure
        || std::env::var("JUMPSTARTER_GRPC_INSECURE").as_deref() == Ok("1")
        || std::env::var("JMP_GRPC_INSECURE").as_deref() == Ok("1")
}

/// Build a `ClientTlsConfig` for `host` from a config `tls` block: the base64 CA
/// when present, else the system roots.
fn tls_config_for(tls: &TlsConfig, host: &str) -> Result<ClientTlsConfig, ClientError> {
    let mut cfg = ClientTlsConfig::new().domain_name(host.to_string());
    if tls.ca.is_empty() {
        cfg = cfg.with_native_roots();
    } else {
        let pem = base64::engine::general_purpose::STANDARD
            .decode(tls.ca.as_bytes())
            .map_err(|e| ClientError::Config(format!("tls.ca is not valid base64: {e}")))?;
        cfg = cfg.ca_certificate(Certificate::from_pem(pem));
    }
    Ok(cfg)
}

/// Build a TLS endpoint to `target` (`host:port`) with the Python-default
/// keepalive options.
fn tls_endpoint(target: &str, tls: &TlsConfig) -> Result<Endpoint, ClientError> {
    let host = target.split(':').next().unwrap_or(target);
    let endpoint = Channel::from_shared(format!("https://{target}"))
        .map_err(|e| ClientError::Config(format!("invalid endpoint {target}: {e}")))?
        .tls_config(tls_config_for(tls, host)?)?
        // Defaults from `_override_default_grpc_options` (common/grpc.py).
        .http2_keep_alive_interval(Duration::from_secs(20))
        .keep_alive_timeout(Duration::from_secs(180))
        .keep_alive_while_idle(true);
    Ok(endpoint)
}

/// Connect a `Channel` to `target`, using insecure (skip-verify) TLS when
/// configured, otherwise verifying against the config CA / system roots.
async fn connect_channel(target: &str, tls: &TlsConfig) -> Result<Channel, ClientError> {
    if is_insecure(tls) {
        crate::insecure::connect(target).await
    } else {
        tls_endpoint(target, tls)?
            .connect()
            .await
            .map_err(Into::into)
    }
}

/// Adds the bearer token and identity metadata to every controller request.
#[derive(Clone)]
pub struct AuthInterceptor {
    bearer: MetadataValue<tonic::metadata::Ascii>,
    kind: MetadataValue<tonic::metadata::Ascii>,
    namespace: MetadataValue<tonic::metadata::Ascii>,
    name: MetadataValue<tonic::metadata::Ascii>,
}

impl AuthInterceptor {
    /// Build the interceptor for a given role (`"Client"`/`"Exporter"`).
    pub fn new(kind: &str, config: &ClientConfig) -> Result<Self, ClientError> {
        let token = config
            .token
            .as_deref()
            .ok_or_else(|| ClientError::Config("token not set in client config".into()))?;
        let namespace = config.metadata.namespace.as_deref().unwrap_or("");
        let parse = |s: &str| {
            s.parse::<MetadataValue<_>>()
                .map_err(|e| ClientError::Config(format!("invalid metadata value: {e}")))
        };
        Ok(Self {
            bearer: parse(&format!("Bearer {token}"))?,
            kind: parse(kind)?,
            namespace: parse(namespace)?,
            name: parse(&config.metadata.name)?,
        })
    }
}

impl Interceptor for AuthInterceptor {
    fn call(&mut self, mut req: Request<()>) -> Result<Request<()>, Status> {
        let md = req.metadata_mut();
        md.insert("authorization", self.bearer.clone());
        md.insert("jumpstarter-kind", self.kind.clone());
        md.insert("jumpstarter-namespace", self.namespace.clone());
        md.insert("jumpstarter-name", self.name.clone());
        Ok(req)
    }
}

/// Build (but do not yet connect) a controller [`Endpoint`] with TLS.
pub fn endpoint(config: &ClientConfig) -> Result<Endpoint, ClientError> {
    let target = config
        .endpoint
        .as_deref()
        .ok_or_else(|| ClientError::Config("endpoint not set in client config".into()))?;
    tls_endpoint(target, &config.tls)
}

/// Connect an authenticated controller channel from a client config.
pub async fn connect(
    config: &ClientConfig,
) -> Result<InterceptedService<Channel, AuthInterceptor>, ClientError> {
    let target = config
        .endpoint
        .as_deref()
        .ok_or_else(|| ClientError::Config("endpoint not set in client config".into()))?;
    let channel = connect_channel(target, &config.tls).await?;
    let interceptor = AuthInterceptor::new("Client", config)?;
    Ok(InterceptedService::new(channel, interceptor))
}

/// Adds only `authorization: Bearer <token>` — used for router streams, whose
/// per-stream `router_token` is the sole credential (`common/streams.py:40-47`).
#[derive(Clone)]
pub struct BearerInterceptor {
    bearer: MetadataValue<tonic::metadata::Ascii>,
}

impl BearerInterceptor {
    pub fn new(token: &str) -> Result<Self, ClientError> {
        let bearer = format!("Bearer {token}")
            .parse()
            .map_err(|e| ClientError::Config(format!("invalid router token: {e}")))?;
        Ok(Self { bearer })
    }
}

impl Interceptor for BearerInterceptor {
    fn call(&mut self, mut req: Request<()>) -> Result<Request<()>, Status> {
        req.metadata_mut()
            .insert("authorization", self.bearer.clone());
        Ok(req)
    }
}

/// Connect a router channel to `endpoint` (`host:port` from a `DialResponse`),
/// authenticated with the per-stream `token` and TLS from `tls`.
pub async fn connect_router(
    endpoint: &str,
    token: &str,
    tls: &TlsConfig,
) -> Result<InterceptedService<Channel, BearerInterceptor>, ClientError> {
    let channel = connect_channel(endpoint, tls).await?;
    Ok(InterceptedService::new(
        channel,
        BearerInterceptor::new(token)?,
    ))
}
