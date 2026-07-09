//! The Jumpstarter controller manager (`/manager` in the controller image).
//!
//! Bootstrap, mirroring the wiring order of `controller/cmd/main.go`:
//!
//! 1. parse Go-`flag`-style arguments (parse errors print usage and exit 2,
//!    `-h`/`-help` exits 0 — the Go `flag.ExitOnError` behavior);
//! 2. initialize tracing (the zap flags are accepted for deployment
//!    compatibility; `zap-log-level` maps onto the tracing filter);
//! 3. resolve the watch namespace (`NAMESPACE` env > service-account file >
//!    fatal — the single-namespace requirement since 0.8.0);
//! 4. build the Kubernetes client (Go: `ctrl.GetConfigOrDie` + `NewManager`);
//! 5. load the `jumpstarter-controller` ConfigMap (fatal on any error) and the
//!    internal signer + token validator + router config;
//! 6. start the health-probe server (`:8081`) and the metrics server (per
//!    flag), the internal OIDC discovery server (`:8085`), and the login
//!    service (`:8086`) — these run on **all** replicas, leader or not
//!    (controller-runtime starts them outside the leader-gated runnable set;
//!    the Go OIDC/login services return `NeedLeaderElection()=false`);
//! 7. when `--leader-elect` is set, contend for the coordination/v1 Lease
//!    `a38b78e7.jumpstarter.dev`; the gRPC service (`:8082`) **and** the
//!    reconcilers are leader-gated (Go: bare `mgr.Add` runnables without
//!    `NeedLeaderElection`), so they start only after the Lease is acquired
//!    and losing it is fatal;
//! 8. serve gRPC on `:8082` over TLS (external cert paths or self-signed, ALPN
//!    `["http/1.1", "h2"]`) with the real ControllerService + ClientService +
//!    grpc-health (`SERVING`) + reflection;
//! 9. exit 0 on SIGTERM/SIGINT (graceful drain), exit 1 on lost leadership.

use std::process::exit;
use std::sync::Arc;

use jumpstarter_controller_api::go_duration::parse_go_duration;
use jumpstarter_controller_auth::signer::Signer;
use jumpstarter_controller_auth::validator::TokenValidator;
use jumpstarter_controller_auth::{discovery, router_token};
use jumpstarter_controller_config::env;
use jumpstarter_controller_config::router::Router;
use jumpstarter_controller_config::types::{Authentication, Config};
use jumpstarter_controller_runtime::configmap::{self, ControllerConfiguration};
use jumpstarter_controller_runtime::flags::{FlagError, Flags};
use jumpstarter_controller_runtime::tls::{
    controller_alpn, controller_endpoint, resolve_server_tls, CONTROLLER_COMMON_NAME,
};
use jumpstarter_controller_runtime::{health, leader, logging, metrics, namespace};
use jumpstarter_controller_service::listen_registry::ListenRegistry;
use jumpstarter_controller_service::login::{self, LoginConfig, OidcConfig};
use jumpstarter_controller_service::server::{self, ServerConfig};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;
use tokio_stream::wrappers::TcpListenerStream;
use tokio_stream::{Stream, StreamExt};
use tracing::{debug, error, info, warn};

/// gRPC listen address (`tls.Listen("tcp", ":8082")` in
/// `controller_service.go`).
const GRPC_LISTEN_ADDR: &str = ":8082";

/// Leader-election Lease name (`LeaderElectionID` in `controller/cmd/main.go:173`).
const LEADER_ELECTION_LEASE: &str = "a38b78e7.jumpstarter.dev";

/// `defaultMaxTags` (`controller_service.go:198`): the effective tag cap when
/// `LeasePolicy.MaxTags` is unset/zero.
const DEFAULT_MAX_TAGS: i32 = 10;

/// The internal OIDC issuer URL; login skips this provider in its OIDC list
/// (`cmd/main.go:jwtAuthenticatorsToOIDCConfigs`).
const INTERNAL_ISSUER_URL: &str = "https://localhost:8085";

/// Version information — the Go binary injects these via ldflags at build
/// time and defaults them to `dev`/`unknown` (`controller/cmd/main.go:64-67`);
/// the container build can set the corresponding env vars at compile time.
const VERSION: &str = match option_env!("JUMPSTARTER_VERSION") {
    Some(v) => v,
    None => "dev",
};
const GIT_COMMIT: &str = match option_env!("JUMPSTARTER_GIT_COMMIT") {
    Some(v) => v,
    None => "unknown",
};
const BUILD_DATE: &str = match option_env!("JUMPSTARTER_BUILD_DATE") {
    Some(v) => v,
    None => "unknown",
};

/// Usage text in the Go `flag` package format (`Usage of /manager:` plus one
/// entry per defined flag), printed to stderr on parse errors and `-h`.
fn usage() -> String {
    "\
Usage of /manager:
  -enable-http2
    \tIf set, HTTP/2 will be enabled for the metrics and webhook servers
  -health-probe-bind-address string
    \tThe address the probe endpoint binds to. (default \":8081\")
  -leader-elect
    \tEnable leader election for controller manager. Enabling this will ensure there is only one active controller manager.
  -metrics-bind-address string
    \tThe address the metric endpoint binds to. Use the port :8080. If not set, it will be 0 in order to disable the metrics server (default \"0\")
  -metrics-secure
    \tIf set the metrics endpoint is served securely
  -zap-devel
    \tDevelopment Mode defaults(encoder=consoleEncoder,logLevel=Debug,stackTraceLevel=Warn). Production Mode defaults(encoder=jsonEncoder,logLevel=Info,stackTraceLevel=Error)
  -zap-encoder value
    \tZap log encoding (one of 'json' or 'console')
  -zap-log-level value
    \tZap Level to configure the verbosity of logging. Can be one of 'debug', 'info', 'error', 'panic' or any integer value > 0 which corresponds to custom debug levels of increasing verbosity
  -zap-stacktrace-level value
    \tZap Level at and above which stacktraces are captured (one of 'info', 'error', 'panic')
  -zap-time-encoding value
    \tZap time encoding (one of 'epoch', 'millis', 'nano', 'iso8601', 'rfc3339' or 'rfc3339nano'). Defaults to 'epoch'.
"
    .to_string()
}

/// Parse argv with Go `flag` semantics: errors print the message + usage to
/// stderr and exit 2; `-h`/`-help` prints usage and exits 0 (both are the
/// `flag.ExitOnError` behaviors — this runs before tracing is initialized,
/// exactly like Go where `flag.Parse` precedes `ctrl.SetLogger`).
fn parse_flags_or_exit() -> Flags {
    match Flags::parse_env() {
        Ok(flags) => flags,
        Err(FlagError::Help) => {
            eprint!("{}", usage());
            exit(0);
        }
        Err(err) => {
            eprintln!("{err}");
            eprint!("{}", usage());
            exit(2);
        }
    }
}

/// Wait for SIGTERM or SIGINT (Go: `ctrl.SetupSignalHandler()` listens for
/// exactly these two). Returns the signal name for logging.
async fn wait_for_termination_signal() -> &'static str {
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigterm = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    let mut sigint = signal(SignalKind::interrupt()).expect("install SIGINT handler");
    tokio::select! {
        _ = sigterm.recv() => "SIGTERM",
        _ = sigint.recv() => "SIGINT",
    }
}

/// A future that resolves once the shutdown flag flips to `true` (or the
/// sender is gone).
async fn shutdown_wait(mut rx: watch::Receiver<bool>) {
    while !*rx.borrow_and_update() {
        if rx.changed().await.is_err() {
            break;
        }
    }
}

/// Bind a TCP listener for a Go-style listen address: the host-less `":8082"`
/// form means "all interfaces" (`tls.Listen("tcp", ":8082")`), mapped to the
/// IPv6 wildcard with an IPv4 fallback (same mapping as the runtime crate's
/// health/metrics servers).
async fn bind_go_listen_addr(addr: &str) -> std::io::Result<TcpListener> {
    if let Some(port) = addr.strip_prefix(':') {
        match TcpListener::bind(format!("[::]:{port}")).await {
            Ok(listener) => Ok(listener),
            Err(_) => TcpListener::bind(format!("0.0.0.0:{port}")).await,
        }
    } else {
        TcpListener::bind(addr).await
    }
}

/// TLS-terminate `listener` with `acceptor`, yielding the connection stream
/// tonic's `serve_with_incoming` consumes. Handshake failures surface as
/// `io::Error` items, which tonic's accept loop logs and skips
/// (`handle_accept_error`: `InvalidData`/`UnexpectedEof` continue).
fn tls_incoming(
    listener: TcpListener,
    acceptor: tokio_rustls::TlsAcceptor,
) -> impl Stream<Item = std::io::Result<tokio_rustls::server::TlsStream<TcpStream>>> {
    TcpListenerStream::new(listener).then(move |conn| {
        let acceptor = acceptor.clone();
        async move {
            match conn {
                Ok(tcp) => acceptor.accept(tcp).await,
                Err(err) => Err(err),
            }
        }
    })
}

/// Leader-election identity, mirroring controller-runtime
/// (`pkg/leaderelection/leader_election.go:88-94`): `<hostname>_<uuid>`.
/// Go mints a v1 UUID; v4 is a benign divergence — the identity is opaque.
fn leader_election_identity() -> std::io::Result<String> {
    let hostname = hostname::get()?;
    Ok(format!(
        "{}_{}",
        hostname.to_string_lossy(),
        uuid::Uuid::new_v4()
    ))
}

/// The auth/config inputs the services need, resolved from the loaded
/// [`ControllerConfiguration`] (the new `config` key or the legacy
/// `authentication` key).
struct ResolvedConfig {
    authentication: Authentication,
    provisioning: bool,
    max_tags: i32,
    router: Router,
    oidc: Vec<OidcConfig>,
}

/// `LeasePolicy.effectiveMaxTags` (`controller_service.go:199-204`).
fn effective_max_tags(configured: i32) -> i32 {
    if configured > 0 {
        configured
    } else {
        DEFAULT_MAX_TAGS
    }
}

/// Build the login OIDC provider list from the configured JWT authenticators,
/// skipping the internal issuer (`jwtAuthenticatorsToOIDCConfigs`,
/// `cmd/main.go`). `clientId` defaults to `"jumpstarter-cli"`.
fn oidc_configs(authentication: &Authentication) -> Vec<OidcConfig> {
    authentication
        .jwt
        .iter()
        .filter(|jwt| jwt.issuer.url != INTERNAL_ISSUER_URL)
        .map(|jwt| OidcConfig {
            issuer: jwt.issuer.url.clone(),
            client_id: "jumpstarter-cli".to_string(),
            audiences: jwt.issuer.audiences.clone(),
        })
        .collect()
}

/// Resolve the auth/router/policy inputs from the loaded configuration. The
/// legacy `authentication` key is a raw YAML string that must still be parsed
/// into an [`Authentication`] (Go `LoadConfiguration` legacy branch); the legacy
/// path has no provisioning/lease-policy config, so both take their defaults.
fn resolve_config(
    configuration: ControllerConfiguration<Config, Router>,
) -> Result<ResolvedConfig, String> {
    match configuration {
        ControllerConfiguration::Config { config, router } => {
            let oidc = oidc_configs(&config.authentication);
            Ok(ResolvedConfig {
                authentication: config.authentication,
                provisioning: config.provisioning.enabled,
                max_tags: effective_max_tags(config.lease_policy.max_tags),
                router,
                oidc,
            })
        }
        ControllerConfiguration::Legacy {
            authentication,
            router,
        } => {
            let authentication: Authentication = serde_yaml_ng::from_str(&authentication)
                .map_err(|err| format!("unable to parse legacy authentication config: {err}"))?;
            let oidc = oidc_configs(&authentication);
            Ok(ResolvedConfig {
                authentication,
                provisioning: false,
                max_tags: DEFAULT_MAX_TAGS,
                router,
                oidc,
            })
        }
    }
}

/// Build the internal ES256 signer (`CONTROLLER_KEY`/PEM override), applying the
/// configured `authentication.internal.tokenLifetime` when present.
fn build_signer(authentication: &Authentication) -> Result<Signer, String> {
    let mut signer = Signer::from_env()
        .map_err(|err| format!("unable to create internal oidc signer: {err}"))?;
    let lifetime = authentication.internal.token_lifetime.trim();
    if !lifetime.is_empty() {
        match parse_go_duration(lifetime) {
            Ok(duration) if duration.nanos() > 0 => {
                signer.set_token_lifetime(std::time::Duration::from_nanos(duration.nanos() as u64));
            }
            // Go fatals on a non-positive lifetime; the port only warns so a
            // misconfigured lifetime does not take the whole controller down
            // (documented divergence — issuance-only, defaults to 365d).
            Ok(_) => warn!(lifetime, "ignoring non-positive internal.tokenLifetime"),
            Err(err) => {
                warn!(lifetime, error = %err, "ignoring unparseable internal.tokenLifetime")
            }
        }
    }
    Ok(signer)
}

#[tokio::main]
async fn main() {
    // Go `flag` parses before the logger exists; keep that order.
    let flags = parse_flags_or_exit();

    // Install the process-wide rustls crypto provider before any TLS use
    // (kube client, the gRPC listener, and the discovery listener all build
    // rustls configs).
    let _ = rustls::crypto::ring::default_provider().install_default();

    logging::init_tracing(flags.zap.log_level.as_ref());
    flags.warn_unsupported_zap_flags();

    info!(
        version = VERSION,
        gitCommit = GIT_COMMIT,
        buildDate = BUILD_DATE,
        "Jumpstarter Controller starting"
    );

    if flags.metrics_secure {
        warn!("metrics-secure is accepted but not implemented: metrics are served over plain HTTP");
    }
    if flags.enable_http2 {
        debug!("enable-http2 has no effect: no webhook server, metrics are plain HTTP");
    }

    // Namespace resolution is fatal before anything talks to the apiserver
    // (`controller/cmd/main.go:188-198`).
    let watch_namespace = match namespace::get_watch_namespace() {
        Ok(ns) => ns,
        Err(err) => {
            error!("{err}");
            exit(1);
        }
    };

    // Go: `ctrl.GetConfigOrDie()` + `ctrl.NewManager` — kubeconfig/in-cluster
    // inference failure is fatal with "unable to start manager".
    let client = match kube::Client::try_default().await {
        Ok(client) => client,
        Err(err) => {
            error!(error = %err, "unable to start manager");
            exit(1);
        }
    };

    // ConfigMap load (`config.LoadConfiguration`, fatal on error). Go quirk
    // preserved: the ObjectKey namespace is the raw NAMESPACE env var — NOT the
    // resolved watch namespace (`controller/cmd/main.go:227`).
    let configmap_namespace = std::env::var(env::NAMESPACE).unwrap_or_default();
    let configuration = match configmap::load_controller_configuration::<Config, Router>(
        client.clone(),
        &configmap_namespace,
    )
    .await
    {
        Ok(configuration) => configuration,
        Err(err) => {
            error!(error = %err, "unable to load configuration");
            exit(1);
        }
    };
    match &configuration {
        ControllerConfiguration::Legacy { router, .. } => {
            info!(
                routers = router.len(),
                "loaded configuration (legacy authentication key)"
            );
        }
        ControllerConfiguration::Config { config, router } => {
            info!(
                routers = router.len(),
                provisioning = config.provisioning.enabled,
                jwt_authenticators = config.authentication.jwt.len(),
                "loaded configuration"
            );
        }
    }

    // Resolve auth/router/policy inputs, build the internal signer + the union
    // token validator, and the shared listen registry / router key.
    let resolved = match resolve_config(configuration) {
        Ok(resolved) => resolved,
        Err(err) => {
            error!(error = %err, "unable to load configuration");
            exit(1);
        }
    };
    let signer = match build_signer(&resolved.authentication) {
        Ok(signer) => Arc::new(signer),
        Err(err) => {
            error!(error = %err, "unable to start manager");
            exit(1);
        }
    };
    let validator = match TokenValidator::load(&resolved.authentication, signer.clone()) {
        Ok(validator) => Arc::new(validator),
        Err(err) => {
            error!(error = %err, "unable to load authentication configuration");
            exit(1);
        }
    };
    let registry = Arc::new(ListenRegistry::new());
    let router_key = router_token::router_key_from_env();

    // Shutdown fan-out: one signal task flips the flag; every server drains.
    let (shutdown_tx, shutdown_rx) = watch::channel(false);
    tokio::spawn(async move {
        let signal = wait_for_termination_signal().await;
        info!(signal, "received termination signal, shutting down");
        let _ = shutdown_tx.send(true);
    });

    // Health probes and metrics serve on every replica, leader or not
    // (controller-runtime starts them outside the leader-gated runnable set).
    let health_server = match health::serve(
        &flags.health_probe_bind_address,
        shutdown_wait(shutdown_rx.clone()),
    )
    .await
    {
        Ok(server) => server,
        Err(err) => {
            error!(error = %err, "unable to start health probe server");
            exit(1);
        }
    };
    let metrics_server = match metrics::serve(
        &flags.metrics_bind_address,
        shutdown_wait(shutdown_rx.clone()),
    )
    .await
    {
        Ok(server) => server,
        Err(err) => {
            error!(error = %err, "unable to start metrics server");
            exit(1);
        }
    };

    // Internal OIDC discovery (:8085) and login (:8086) run on ALL replicas
    // (their Go services return `NeedLeaderElection()=false`), so they start
    // before the leader gate.
    {
        let signer = signer.clone();
        tokio::spawn(async move {
            if let Err(err) = discovery::serve_default(signer).await {
                error!(error = %err, "internal OIDC discovery server exited");
            }
        });
    }
    {
        let login_config = LoginConfig::from_env(resolved.oidc.clone());
        let login_addr = login::listen_addr_from_env();
        let login_shutdown = shutdown_wait(shutdown_rx.clone());
        tokio::spawn(async move {
            if let Err(err) = login::serve(login_config, &login_addr, login_shutdown).await {
                error!(error = %err, "login service exited");
            }
        });
    }

    // Leader election: the gRPC service AND the reconcilers are leader-gated in
    // Go (bare `mgr.Add` runnables without `NeedLeaderElection`), so with
    // --leader-elect we only start them once the Lease is acquired, and losing
    // it is fatal.
    let election = if flags.leader_elect {
        let identity = match leader_election_identity() {
            Ok(identity) => identity,
            Err(err) => {
                error!(error = %err, "unable to start manager");
                exit(1);
            }
        };
        let (leader_rx, handle) = leader::spawn_leader_election(
            client.clone(),
            &watch_namespace,
            LEADER_ELECTION_LEASE,
            &identity,
        );
        Some((leader_rx, handle))
    } else {
        None
    };

    if let Some((leader_rx, _)) = &election {
        let mut leader_rx = leader_rx.clone();
        let mut shutdown = shutdown_rx.clone();
        info!(
            lease = LEADER_ELECTION_LEASE,
            namespace = %watch_namespace,
            "waiting for leadership before starting the gRPC service and reconcilers"
        );
        loop {
            if *leader_rx.borrow_and_update() {
                break;
            }
            tokio::select! {
                changed = leader_rx.changed() => {
                    if changed.is_err() {
                        error!("leader election task ended unexpectedly");
                        exit(1);
                    }
                }
                _ = shutdown.changed() => {
                    if *shutdown.borrow() {
                        info!("shutdown requested before leadership was acquired");
                        exit(0);
                    }
                }
            }
        }
    }

    // Leader-gated reconcilers (Exporter + Client + Lease). They run for the
    // lifetime of the process; losing leadership below tears the process down.
    {
        let client = client.clone();
        let signer = signer.clone();
        let watch_namespace = watch_namespace.clone();
        tokio::spawn(async move {
            jumpstarter_controller_core::run(client, signer, watch_namespace).await;
            warn!("reconcilers exited");
        });
    }

    // TLS material with the Go precedence (EXTERNAL_CERT_PEM/EXTERNAL_KEY_PEM
    // file paths, else self-signed with SANs from GRPC_ENDPOINT) and the Go
    // listener's ALPN preference `["http/1.1", "h2"]`.
    let endpoint = controller_endpoint();
    let tls_material = match resolve_server_tls(CONTROLLER_COMMON_NAME, &endpoint).await {
        Ok(material) => material,
        Err(err) => {
            error!(error = %err, "problem running manager");
            exit(1);
        }
    };
    let server_config = match tls_material.into_server_config(controller_alpn()) {
        Ok(config) => config,
        Err(err) => {
            error!(error = %err, "problem running manager");
            exit(1);
        }
    };
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(server_config));

    let listener = match bind_go_listen_addr(GRPC_LISTEN_ADDR).await {
        Ok(listener) => listener,
        Err(err) => {
            error!(error = %err, addr = GRPC_LISTEN_ADDR, "problem running manager");
            exit(1);
        }
    };

    // Assemble the real gRPC service stack (ControllerService + ClientService +
    // health SERVING + reflection).
    let router = match server::build_router(ServerConfig {
        client: client.clone(),
        signer: signer.clone(),
        validator: validator.clone(),
        registry: registry.clone(),
        router: resolved.router.clone(),
        router_key: router_key.clone(),
        provisioning: resolved.provisioning,
        max_tags: resolved.max_tags,
    })
    .await
    {
        Ok(router) => router,
        Err(err) => {
            error!(error = %err, "unable to build gRPC server");
            exit(1);
        }
    };

    info!("Starting Controller grpc service on port 8082");
    let shutdown = {
        let rx = shutdown_rx.clone();
        async move {
            shutdown_wait(rx).await;
            info!("Stopping Controller gRPC service");
        }
    };
    let serve = router.serve_with_incoming_shutdown(tls_incoming(listener, acceptor), shutdown);

    let serve_result = match election {
        Some((_, election_handle)) => {
            tokio::select! {
                result = serve => result,
                lost = election_handle => {
                    // Held-then-lost leadership is fatal, matching
                    // controller-runtime's `leader election lost` error path
                    // (`mgr.Start` returns an error, main exits 1).
                    match lost {
                        Ok(err) => error!(error = %err, "problem running manager"),
                        Err(join_err) => {
                            error!(error = %join_err, "leader election task panicked")
                        }
                    }
                    exit(1);
                }
            }
        }
        None => serve.await,
    };

    if let Err(err) = serve_result {
        error!(error = %err, "problem running manager");
        exit(1);
    }

    // Graceful shutdown: wait for the auxiliary servers to drain too.
    if let Err(err) = health_server.join().await {
        warn!(error = %err, "health probe server shutdown error");
    }
    if let Err(err) = metrics_server.join().await {
        warn!(error = %err, "metrics server shutdown error");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The exact operator-deployed argument vector must select leader election,
    /// the :8081 probes, and :8080 metrics.
    #[test]
    fn operator_arg_vector_selects_bootstrap_inputs() {
        let flags = Flags::parse([
            "--leader-elect",
            "--health-probe-bind-address=:8081",
            "-metrics-bind-address=:8080",
        ])
        .expect("operator arg vector parses");
        assert!(flags.leader_elect);
        assert_eq!(flags.health_probe_bind_address, ":8081");
        assert_eq!(flags.metrics_bind_address, ":8080");
    }

    #[test]
    fn usage_lists_every_supported_flag() {
        let usage = usage();
        for flag in [
            "-enable-http2",
            "-health-probe-bind-address",
            "-leader-elect",
            "-metrics-bind-address",
            "-metrics-secure",
            "-zap-devel",
            "-zap-encoder",
            "-zap-log-level",
            "-zap-stacktrace-level",
            "-zap-time-encoding",
        ] {
            assert!(usage.contains(&format!("\n  {flag}")), "missing {flag}");
        }
        assert!(usage.starts_with("Usage of /manager:"));
    }

    #[test]
    fn leader_identity_shape() {
        let identity = leader_election_identity().expect("hostname resolves");
        let (host, uuid) = identity
            .rsplit_once('_')
            .expect("identity is <hostname>_<uuid>");
        assert!(!host.is_empty());
        assert_eq!(uuid.len(), 36, "uuid text form: {uuid}");
    }

    #[test]
    fn effective_max_tags_defaults_when_unset() {
        assert_eq!(effective_max_tags(0), DEFAULT_MAX_TAGS);
        assert_eq!(effective_max_tags(-1), DEFAULT_MAX_TAGS);
        assert_eq!(effective_max_tags(25), 25);
    }

    #[test]
    fn oidc_configs_skip_internal_issuer() {
        use jumpstarter_controller_config::jwt_authenticator::{Issuer, JwtAuthenticator};
        let authentication = Authentication {
            jwt: vec![
                JwtAuthenticator {
                    issuer: Issuer {
                        url: INTERNAL_ISSUER_URL.to_string(),
                        ..Default::default()
                    },
                    ..Default::default()
                },
                JwtAuthenticator {
                    issuer: Issuer {
                        url: "https://dex.example/dex".to_string(),
                        audiences: vec!["jumpstarter".to_string()],
                        ..Default::default()
                    },
                    ..Default::default()
                },
            ],
            ..Default::default()
        };
        let oidc = oidc_configs(&authentication);
        assert_eq!(oidc.len(), 1);
        assert_eq!(oidc[0].issuer, "https://dex.example/dex");
        assert_eq!(oidc[0].client_id, "jumpstarter-cli");
        assert_eq!(oidc[0].audiences, vec!["jumpstarter".to_string()]);
    }
}
