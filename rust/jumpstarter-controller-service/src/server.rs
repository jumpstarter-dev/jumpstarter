//! Assembly of the controller gRPC surface (`:8082`), a port of the service
//! registration in `ControllerService.Start` (`controller_service.go:1092-1190`):
//! `RegisterControllerServiceServer` + `RegisterClientServiceServer` + the
//! standard gRPC health service (server-wide `SERVING`) + server reflection.
//!
//! This module builds the tonic service stack (a [`tonic::transport::server::Router`])
//! and leaves the transport to the caller: the manager binary owns the
//! TLS-terminated listener (runtime `tls` material + the Go-parity ALPN list)
//! and drives `serve_with_incoming_shutdown`, exactly as it already does for the
//! phase-1 stub. Keeping the transport in the bin means the service crate needs
//! no listener/TLS plumbing and stays unit-testable.
//!
//! The whole stack is **leader-gated**: Go registers `ControllerService` as a
//! bare `mgr.Add` runnable (no `NeedLeaderElection`), so gRPC serves only on the
//! elected leader. The manager reproduces that by building + serving this router
//! only after acquiring leadership.

use std::sync::Arc;

use thiserror::Error;
use tonic::transport::server::Router as TonicRouter;
use tonic::transport::Server;
use tonic_health::ServingStatus;

use jumpstarter_controller_auth::signer::Signer;
use jumpstarter_controller_auth::validator::TokenValidator;
use jumpstarter_controller_config::router::Router;

use jumpstarter_protocol::client_v1::client_service_server::ClientServiceServer;
use jumpstarter_protocol::v1::controller_service_server::ControllerServiceServer;

use crate::client_service::ClientService;
use crate::controller_service::{ControllerAuth, ControllerService};
use crate::listen_registry::ListenRegistry;

/// The gRPC-advertised service names — used both to build the reflection
/// service and (in tests) to assert the served surface matches Go's.
pub const SERVICE_NAMES: &[&str] = &[
    "jumpstarter.v1.ControllerService",
    "jumpstarter.client.v1.ClientService",
    "grpc.health.v1.Health",
    "grpc.reflection.v1.ServerReflection",
    "grpc.reflection.v1alpha.ServerReflection",
];

/// Everything the gRPC server needs, resolved by the manager from the loaded
/// configuration + the internal signer.
pub struct ServerConfig {
    /// Kubernetes client used by every RPC's CR reads/writes.
    pub client: kube::Client,
    /// Internal ES256 signer — `ClientService.RotateToken` re-mints tokens with
    /// it; also the internal token validator authenticates against its key.
    pub signer: Arc<Signer>,
    /// The union token validator (external issuers + the internal signer).
    pub validator: Arc<TokenValidator>,
    /// Shared exporter `Listen`-queue registry (Dial ↔ Listen rendezvous).
    pub registry: Arc<ListenRegistry>,
    /// Router endpoint/label config (`config.Router`) for Dial selection.
    pub router: Router,
    /// HS256 router-token key (`[]byte(os.Getenv("ROUTER_KEY"))`).
    pub router_key: Vec<u8>,
    /// Whether Client CRs are auto-provisioned on first auth.
    pub provisioning: bool,
    /// `LeasePolicy.MaxTags` effective value (already defaulted to 10 when 0).
    pub max_tags: i32,
}

/// Errors assembling the server stack.
#[derive(Debug, Error)]
pub enum ServerError {
    #[error("failed to build reflection service: {0}")]
    Reflection(#[from] tonic_reflection::server::Error),
}

/// The reflection builder advertising exactly [`SERVICE_NAMES`] (the same list
/// Go's `reflection.Register` derives from the running server).
pub fn reflection_builder() -> tonic_reflection::server::Builder<'static> {
    let mut builder = tonic_reflection::server::Builder::configure()
        .register_encoded_file_descriptor_set(jumpstarter_protocol::FILE_DESCRIPTOR_SET)
        .register_encoded_file_descriptor_set(tonic_health::pb::FILE_DESCRIPTOR_SET)
        .register_encoded_file_descriptor_set(tonic_reflection::pb::v1::FILE_DESCRIPTOR_SET)
        .register_encoded_file_descriptor_set(tonic_reflection::pb::v1alpha::FILE_DESCRIPTOR_SET);
    for name in SERVICE_NAMES {
        builder = builder.with_service_name(*name);
    }
    builder
}

/// Build the full tonic service stack: the real `ControllerService` +
/// `ClientService`, the gRPC health service (server-wide `SERVING`), and both
/// reflection versions. The caller serves the returned router over its
/// TLS-terminated incoming stream.
pub async fn build_router(config: ServerConfig) -> Result<TonicRouter, ServerError> {
    // The shared per-call authenticator, used by both services. The authorizer
    // membership prefix is the validator's resolved internal prefix (Go's
    // second `LoadConfiguration` return, threaded into `NewBasicAuthorizer`).
    let auth = Arc::new(ControllerAuth::new(
        config.client.clone(),
        config.validator.clone(),
        config.validator.internal_prefix().to_string(),
        config.provisioning,
    ));

    let controller = ControllerService::new(
        config.client.clone(),
        auth.clone(),
        config.registry.clone(),
        config.router.clone(),
        config.router_key.clone(),
    );

    // ClientService is generic over the AuthClient seam; the shared
    // `Arc<ControllerAuth>` satisfies it (namespace-matched client verify).
    let client_service = ClientService::new(
        config.client.clone(),
        auth.clone(),
        config.max_tags,
        config.signer.clone(),
    );

    // grpc.health.v1.Health: the "" (server-wide) service is SERVING, mirroring
    // `hs.SetServingStatus("", HealthCheckResponse_SERVING)`.
    let (mut health_reporter, health_service) = tonic_health::server::health_reporter();
    health_reporter
        .set_service_status("", ServingStatus::Serving)
        .await;

    let reflection_v1 = reflection_builder().build_v1()?;
    let reflection_v1alpha = reflection_builder().build_v1alpha()?;

    Ok(Server::builder()
        .add_service(ControllerServiceServer::new(controller))
        .add_service(ClientServiceServer::new(client_service))
        .add_service(health_service)
        .add_service(reflection_v1)
        .add_service(reflection_v1alpha))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_names_cover_go_surface() {
        // Exactly the five services Go's server advertises via reflection.
        assert_eq!(SERVICE_NAMES.len(), 5);
        assert!(SERVICE_NAMES.contains(&"jumpstarter.v1.ControllerService"));
        assert!(SERVICE_NAMES.contains(&"jumpstarter.client.v1.ClientService"));
    }

    #[test]
    fn reflection_builds_both_versions() {
        reflection_builder().build_v1().expect("reflection v1");
        reflection_builder()
            .build_v1alpha()
            .expect("reflection v1alpha");
    }
}
