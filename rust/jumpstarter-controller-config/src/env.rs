//! Environment variable names consumed by the controller and router binaries.
//!
//! The set is verified against every `os.Getenv` in the Go controller tree
//! (excluding tests and the operator); the operator's Deployment builders in
//! `controller/deploy/operator/internal/controller/jumpstarter/jumpstarter_controller.go`
//! are the writers.

/// Kubernetes namespace the controller/router operates in. Consumed by
/// `cmd/main.go`, `cmd/router/main.go` and the login service; injected by the
/// operator via the Downward API (`metadata.namespace`).
pub const NAMESPACE: &str = "NAMESPACE";

/// Secret seed for the internal token signer (`cmd/main.go` — fed to the
/// deterministic ES256 key derivation). Injected from the
/// `jumpstarter-controller-secret` Secret, key `key`.
pub const CONTROLLER_KEY: &str = "CONTROLLER_KEY";

/// HMAC secret for router stream tokens: the controller signs Dial tokens
/// with it (`internal/service/controller_service.go`) and the router
/// validates them (`internal/service/router_service.go`). Injected from the
/// `jumpstarter-router-secret` Secret, key `key`.
pub const ROUTER_KEY: &str = "ROUTER_KEY";

/// Public gRPC endpoint of the controller, advertised to clients/exporters
/// (`internal/controller/endpoints.go`, `internal/service/endpoints.go`,
/// login service). Defaults to [`DEFAULT_GRPC_ENDPOINT`] when unset.
pub const GRPC_ENDPOINT: &str = "GRPC_ENDPOINT";

/// Public gRPC endpoint a router replica advertises itself as
/// (`internal/service/endpoints.go`, login service). Defaults to
/// [`DEFAULT_GRPC_ROUTER_ENDPOINT`] when unset.
pub const GRPC_ROUTER_ENDPOINT: &str = "GRPC_ROUTER_ENDPOINT";

/// Public URL of the login service, shown on the landing page
/// (`internal/service/login/service.go`).
pub const LOGIN_ENDPOINT: &str = "LOGIN_ENDPOINT";

/// Listen address of the login service; either a bare port ("8086") or a
/// host:port (`internal/service/login/service.go`). Defaults to
/// [`DEFAULT_LOGIN_SERVICE_PORT`] when unset.
pub const LOGIN_SERVICE_PORT: &str = "LOGIN_SERVICE_PORT";

/// PEM CA bundle the login service returns to clients (base64-encoded in the
/// `/v1/auth/config` response). Injected from the CA ConfigMap, key `ca.crt`.
pub const CA_BUNDLE_PEM: &str = "CA_BUNDLE_PEM";

/// Path to the external TLS certificate for the gRPC listeners
/// (`internal/service/controller_service.go`, `router_service.go`). The
/// operator sets it to "/tls/tls.crt" when TLS material is provisioned;
/// unset means a self-signed certificate is generated.
pub const EXTERNAL_CERT_PEM: &str = "EXTERNAL_CERT_PEM";

/// Path to the external TLS private key, companion to [`EXTERNAL_CERT_PEM`]
/// ("/tls/tls.key" when set by the operator).
pub const EXTERNAL_KEY_PEM: &str = "EXTERNAL_KEY_PEM";

/// Run mode of the gin HTTP framework backing the Go login service; the
/// operator sets it to "release". Consumed by the gin library itself, not by
/// jumpstarter code — kept for env-parity with the Go deployment (a Rust
/// login service ignores it).
pub const GIN_MODE: &str = "GIN_MODE";

/// Go `controllerEndpoint()` fallback when [`GRPC_ENDPOINT`] is unset.
pub const DEFAULT_GRPC_ENDPOINT: &str = "localhost:8082";

/// Go `routerEndpoint()` fallback when [`GRPC_ROUTER_ENDPOINT`] is unset.
pub const DEFAULT_GRPC_ROUTER_ENDPOINT: &str = "localhost:8083";

/// Go login-service `defaultPort` fallback when [`LOGIN_SERVICE_PORT`] is
/// unset (already in listen-address form).
pub const DEFAULT_LOGIN_SERVICE_PORT: &str = ":8086";
