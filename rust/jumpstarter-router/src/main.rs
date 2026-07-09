//! The Jumpstarter router (`/router` in the controller image; the operator
//! overrides the container Command to select it — no args, **no probes**).
//!
//! Mirrors `controller/cmd/router/main.go`:
//!
//! 1. parse flags — the Go router binds ONLY the zap flags
//!    (`zap.Options.BindFlags`), so the manager flags are rejected here too;
//!    parse errors print usage and exit 2, `-h` exits 0;
//! 2. initialize tracing and log the version banner;
//! 3. build the Kubernetes client (Go: `ctrl.GetConfigOrDie` + `kclient.New`
//!    — fatal with "failed to create k8s client");
//! 4. load the router configuration from the `jumpstarter-controller`
//!    ConfigMap in `$NAMESPACE` (`config.LoadRouterConfiguration`: only the
//!    `config` key, of which only the `grpc` keepalive section is consumed;
//!    absent keepalive fields tolerate — Go `config.ParseDuration("") == 0`)
//!    — fatal with "failed to load router configuration". Note there is
//!    **no** namespace-file fallback here: the Go router reads the raw
//!    `NAMESPACE` env var, so an unset variable fails at the ConfigMap
//!    fetch;
//! 5. serve the production `jumpstarter-router-service` rendezvous on
//!    `:8083` over TLS (external cert paths or self-signed with SANs from
//!    `GRPC_ROUTER_ENDPOINT`, ALPN `["h2"]`) with server reflection, the
//!    keepalive server options from step 4, the gzip codec registered
//!    exactly like the Go blank import (`cmd/router/main.go:34`: gzip
//!    requests accepted, responses compressed only as a mirror of the
//!    request's encoding — see `jumpstarter_router_service::compression`),
//!    and tonic's default 4 MiB receive limit (grpc-go's default — neither
//!    router overrides it). Unlike the controller there is **no** health
//!    service (`router_service.go` registers only RouterService +
//!    reflection) and no health/metrics HTTP servers;
//! 6. wait for SIGTERM/SIGINT and exit 0 ("received signal, exiting")
//!    **immediately** — a hard stop with no graceful drain. The router's
//!    only RPC is an indefinite bidi tunnel, so a drain could never
//!    complete; the Go router dies instantly on SIGTERM (see
//!    [`serve_until_signal`]).

use std::future::Future;
use std::process::exit;
use std::sync::Arc;

use jumpstarter_controller_config::env;
use jumpstarter_controller_config::types::Config;
use jumpstarter_controller_runtime::configmap;
use jumpstarter_controller_runtime::flags::{FlagError, Flags};
use jumpstarter_controller_runtime::logging;
use jumpstarter_controller_runtime::tls::{
    resolve_server_tls, router_alpn, router_endpoint, ROUTER_COMMON_NAME,
};
use jumpstarter_router_service::compression::MirrorGzipLayer;
use jumpstarter_router_service::{keepalive, RouterService};
use tokio::net::{TcpListener, TcpStream};
use tokio_stream::wrappers::TcpListenerStream;
use tokio_stream::{Stream, StreamExt};
use tonic::transport::Server;
use tracing::{error, info};

/// gRPC listen address (`net.Listen("tcp", ":8083")` in `router_service.go`).
const GRPC_LISTEN_ADDR: &str = ":8083";

/// Version information — the Go binary injects these via ldflags at build
/// time and defaults them to `dev`/`unknown` (`controller/cmd/router/main.go:38-42`).
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

/// Usage text for the router. The Go router binary defines **only** the zap
/// flags (`zap.Options.BindFlags` + `flag.Parse` in `cmd/router/main.go`);
/// [`Flags::parse_router_env`] enforces the same surface, so manager flags
/// are "not defined" errors here exactly as in Go.
fn usage() -> String {
    "\
Usage of /router:
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

/// Parse argv with Go `flag.ExitOnError` semantics (see the manager binary)
/// against the router's zap-only flag surface.
fn parse_flags_or_exit() -> Flags {
    match Flags::parse_router_env() {
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

/// Wait for SIGTERM or SIGINT (`signal.Notify(sigs, syscall.SIGINT,
/// syscall.SIGTERM)` in `cmd/router/main.go`). Returns the signal name.
async fn wait_for_termination_signal() -> &'static str {
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigterm = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    let mut sigint = signal(SignalKind::interrupt()).expect("install SIGINT handler");
    tokio::select! {
        _ = sigterm.recv() => "SIGTERM",
        _ = sigint.recv() => "SIGINT",
    }
}

/// Outcome of racing the serve future against the first termination signal.
enum ServeOutcome<E> {
    /// The server exited on its own (Go: `svc.Start` returned).
    Server(Result<(), E>),
    /// The first SIGTERM/SIGINT arrived (the signal name).
    Signal(&'static str),
}

/// Race `serve` against the first termination signal; on a signal, return
/// **immediately** without draining in-flight RPCs.
///
/// The router's only RPC is an indefinite bidi tunnel
/// (`RouterService.Stream`), so a graceful drain could never complete — the
/// pod would linger holding tunnels until kubelet's SIGKILL on every
/// rollout. The Go router dies instantly on SIGTERM: its `signal.Notify`
/// (`cmd/router/main.go:87-90`) is dead code because `svc.Start` blocks in
/// `server.Serve` first, leaving the default signal disposition to kill the
/// process — and even the intended stop path is a hard `server.Stop()`, not
/// `GracefulStop` (`router_service.go:170-174`). Deliberate benign
/// divergence: Go's death-by-signal exits with status 143 and no log; here
/// the caller logs "received signal, exiting" and exits 0 (the Go author's
/// intended path), still without any drain.
async fn serve_until_signal<E>(
    serve: impl Future<Output = Result<(), E>>,
    signal: impl Future<Output = &'static str>,
) -> ServeOutcome<E> {
    tokio::select! {
        result = serve => ServeOutcome::Server(result),
        name = signal => ServeOutcome::Signal(name),
    }
}

/// Bind a TCP listener for a Go-style listen address (host-less `":8083"`
/// means all interfaces; IPv6 wildcard with IPv4 fallback).
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

/// TLS-terminate `listener` with `acceptor` for tonic's `serve_with_incoming`
/// (handshake failures are yielded as `io::Error` items, which tonic's accept
/// loop logs and skips). Handshakes run inline — fine for the phase-1 stub.
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

/// Reflection builder advertising exactly what the Go router serves:
/// RouterService plus the reflection services themselves — **no** health
/// service (`router_service.go` registers only RouterService + reflection).
fn reflection_builder() -> tonic_reflection::server::Builder<'static> {
    tonic_reflection::server::Builder::configure()
        .register_encoded_file_descriptor_set(jumpstarter_protocol::FILE_DESCRIPTOR_SET)
        .register_encoded_file_descriptor_set(tonic_reflection::pb::v1::FILE_DESCRIPTOR_SET)
        .register_encoded_file_descriptor_set(tonic_reflection::pb::v1alpha::FILE_DESCRIPTOR_SET)
        .with_service_name("jumpstarter.v1.RouterService")
        .with_service_name("grpc.reflection.v1.ServerReflection")
        .with_service_name("grpc.reflection.v1alpha.ServerReflection")
}

#[tokio::main]
async fn main() {
    let flags = parse_flags_or_exit();

    // Install the process-wide rustls crypto provider before any TLS use.
    let _ = rustls::crypto::ring::default_provider().install_default();

    logging::init_tracing(flags.zap.log_level.as_ref());
    flags.warn_unsupported_zap_flags();

    info!(
        version = VERSION,
        gitCommit = GIT_COMMIT,
        buildDate = BUILD_DATE,
        "Jumpstarter Router starting"
    );

    // Go: `ctrl.GetConfigOrDie()` + `kclient.New` — fatal on failure.
    let client = match kube::Client::try_default().await {
        Ok(client) => client,
        Err(err) => {
            error!(error = %err, "failed to create k8s client");
            exit(1);
        }
    };

    // `config.LoadRouterConfiguration` reads the `config` key of the
    // `jumpstarter-controller` ConfigMap in the raw `$NAMESPACE` (no
    // namespace-file fallback in the Go router; unset env fails the fetch).
    // Only the `grpc` keepalive section is consumed by the Go router; the
    // keepalive parse below is part of Go's LoadRouterConfiguration, so its
    // failure carries the same fatal message.
    let namespace = std::env::var(env::NAMESPACE).unwrap_or_default();
    let config: Config =
        match configmap::load_router_configuration(client.clone(), &namespace).await {
            Ok(config) => config,
            Err(err) => {
                error!(error = %err, "failed to load router configuration");
                exit(1);
            }
        };
    let server_options = match keepalive::load_grpc_configuration(&config.grpc) {
        Ok(options) => options,
        Err(err) => {
            error!(error = %err, "failed to load router configuration");
            exit(1);
        }
    };
    info!(
        keepalive_min_time = %config.grpc.keepalive.min_time,
        keepalive_permit_without_stream = config.grpc.keepalive.permit_without_stream,
        "loaded router configuration"
    );

    // TLS material with the Go precedence (EXTERNAL_CERT_PEM/EXTERNAL_KEY_PEM
    // file paths, else self-signed with SANs from GRPC_ROUTER_ENDPOINT) and
    // grpc-go's h2-only ALPN (`credentials.NewServerTLSFromCert`).
    let endpoint = router_endpoint();
    let tls_material = match resolve_server_tls(ROUTER_COMMON_NAME, &endpoint).await {
        Ok(material) => material,
        Err(err) => {
            error!(error = %err, "failed to start router service");
            exit(1);
        }
    };
    let server_config = match tls_material.into_server_config(router_alpn()) {
        Ok(config) => config,
        Err(err) => {
            error!(error = %err, "failed to start router service");
            exit(1);
        }
    };
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(server_config));

    let listener = match bind_go_listen_addr(GRPC_LISTEN_ADDR).await {
        Ok(listener) => listener,
        Err(err) => {
            error!(error = %err, addr = GRPC_LISTEN_ADDR, "failed to start router service");
            exit(1);
        }
    };

    let reflection_v1 = match reflection_builder().build_v1() {
        Ok(service) => service,
        Err(err) => {
            error!(error = %err, "failed to start router service");
            exit(1);
        }
    };
    let reflection_v1alpha = match reflection_builder().build_v1alpha() {
        Ok(service) => service,
        Err(err) => {
            error!(error = %err, "failed to start router service");
            exit(1);
        }
    };

    info!("Starting grpc router service on port 8083");
    // Keepalive server options from the ConfigMap (Go appends them to the
    // gRPC server options, router_service.go:158); message limits stay at
    // the 4 MiB default on both implementations. Gzip parity with the Go
    // blank import (cmd/router/main.go:34): into_server() accepts/sends
    // gzip and MirrorGzipLayer keeps response compression mirror-only,
    // applied server-wide because Go's codec registration is process-global
    // (reflection mirrors too).
    let serve = keepalive::apply(Server::builder(), &server_options)
        .layer(MirrorGzipLayer)
        .add_service(RouterService::new().into_server())
        .add_service(reflection_v1)
        .add_service(reflection_v1alpha)
        .serve_with_incoming(tls_incoming(listener, acceptor));

    match serve_until_signal(serve, wait_for_termination_signal()).await {
        ServeOutcome::Server(Err(err)) => {
            error!(error = %err, "failed to start router service");
            exit(1);
        }
        // Unreachable in practice: the accept stream never ends, so the
        // server only exits with an error. (Go would fall through to its
        // signal wait here.)
        ServeOutcome::Server(Ok(())) => {}
        ServeOutcome::Signal(signal) => {
            // Hard stop, matching Go (see serve_until_signal): exit now,
            // do NOT drain the indefinite tunnels.
            info!(signal, "received signal, exiting");
            exit(0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_protocol::v1 as pb;

    #[test]
    fn usage_lists_the_go_router_flag_surface() {
        let usage = usage();
        assert!(usage.starts_with("Usage of /router:"));
        for flag in [
            "-zap-devel",
            "-zap-encoder",
            "-zap-log-level",
            "-zap-stacktrace-level",
            "-zap-time-encoding",
        ] {
            assert!(usage.contains(&format!("\n  {flag}")), "missing {flag}");
        }
        // The router usage must not advertise manager-only flags.
        assert!(!usage.contains("-leader-elect"));
        assert!(!usage.contains("-metrics-bind-address"));
    }

    /// The reflection service must build from the protocol crate's descriptor
    /// set.
    #[test]
    fn reflection_builders_accept_the_descriptor_set() {
        reflection_builder().build_v1().expect("v1 reflection");
        reflection_builder()
            .build_v1alpha()
            .expect("v1alpha reflection");
    }

    /// Locks the signal contract (Go parity): the router's only RPC is an
    /// indefinite bidi tunnel, so a graceful drain can never complete. With
    /// a live Stream RPC parked in the rendezvous ("waiting for the other
    /// side"), the first termination signal must win the race immediately —
    /// the Go router dies instantly on SIGTERM (`cmd/router/main.go`'s
    /// `signal.Notify` is dead code; the default disposition kills the
    /// process) and even its intended path is a hard `server.Stop`
    /// (`router_service.go:170-174`). The pre-fix graceful drain
    /// (`serve_with_incoming_shutdown` + post-signal `serve.await`) hangs
    /// here until the timeout.
    #[tokio::test]
    async fn first_signal_wins_immediately_over_an_open_tunnel() {
        use jsonwebtoken::{Algorithm, EncodingKey, Header};
        use std::time::{Duration, SystemTime, UNIX_EPOCH};

        const KEY: &[u8] = b"signal-test-router-key";

        // Plain-TCP variant of the production stack — the property under
        // test (signal beats drain) is independent of TLS termination.
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("local addr");
        let serve = Server::builder()
            .add_service(RouterService::with_static_key(KEY.to_vec()).into_server())
            .serve_with_incoming(TcpListenerStream::new(listener));

        let (signal_tx, signal_rx) = tokio::sync::oneshot::channel::<()>();
        let race = tokio::spawn(serve_until_signal(serve, async move {
            let _ = signal_rx.await;
            "SIGTERM"
        }));

        // Open an authenticated Stream RPC. Response headers arriving means
        // the handler parked this peer in the rendezvous — an in-flight RPC
        // that never completes on its own.
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_secs();
        let token = jsonwebtoken::encode(
            &Header::new(Algorithm::HS256),
            &serde_json::json!({
                "iss": "https://jumpstarter.dev/stream",
                "sub": "signal-test-stream",
                "aud": ["https://jumpstarter.dev/router"],
                "exp": now + 600,
                "nbf": now,
                "iat": now,
            }),
            &EncodingKey::from_secret(KEY),
        )
        .expect("encode token");
        let channel = tonic::transport::Channel::from_shared(format!("http://{addr}"))
            .expect("uri")
            .connect()
            .await
            .expect("connect");
        let mut client = pb::router_service_client::RouterServiceClient::new(channel);
        let (frame_tx, frame_rx) = tokio::sync::mpsc::channel::<pb::StreamRequest>(1);
        let mut request =
            tonic::Request::new(tokio_stream::wrappers::ReceiverStream::new(frame_rx));
        request.metadata_mut().insert(
            "authorization",
            format!("Bearer {token}").parse().expect("metadata value"),
        );
        let inbound = client
            .stream(request)
            .await
            .expect("open tunnel")
            .into_inner();

        // Deliver the first signal while the tunnel is in flight; the race
        // must resolve to the signal branch without waiting for any drain.
        signal_tx.send(()).expect("deliver signal");
        let outcome = tokio::time::timeout(Duration::from_secs(5), race)
            .await
            .expect("signal exit must not wait for the tunnel to drain")
            .expect("serve_until_signal task");
        assert!(matches!(outcome, ServeOutcome::Signal("SIGTERM")));

        // The tunnel handles were held open across the whole race, so the
        // RPC really was in flight when the signal won.
        drop((frame_tx, inbound));
    }

    /// Wire test of the exact serving stack `main` assembles (on port 0, so
    /// it coexists with a real deployment): TLS with grpc-go's h2-only ALPN,
    /// reflection listing exactly the Go router's services (no health), and
    /// the production rendezvous service gating admission on the bearer
    /// token. (The rendezvous/forwarding behavior itself is covered by
    /// jumpstarter-router-service's own test suite.)
    #[tokio::test]
    async fn grpc_stack_serves_tls_reflection_and_stub() {
        use jumpstarter_controller_runtime::tls::TlsMaterial;
        use tonic_reflection::pb::v1::server_reflection_client::ServerReflectionClient;
        use tonic_reflection::pb::v1::server_reflection_request::MessageRequest;
        use tonic_reflection::pb::v1::server_reflection_response::MessageResponse;
        use tonic_reflection::pb::v1::ServerReflectionRequest;

        let _ = rustls::crypto::ring::default_provider().install_default();

        // Test CA + localhost leaf so the client can pin a real trust anchor
        // (webpki rejects a CA certificate used directly as the server cert).
        let mut ca_params = rcgen::CertificateParams::default();
        ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
        let ca_key = rcgen::KeyPair::generate().expect("ca keypair");
        let ca_cert = ca_params.self_signed(&ca_key).expect("ca cert");

        let mut leaf_params = rcgen::CertificateParams::default();
        leaf_params.subject_alt_names = vec![rcgen::SanType::DnsName(
            rcgen::Ia5String::try_from("localhost").expect("ia5"),
        )];
        let leaf_key = rcgen::KeyPair::generate().expect("leaf keypair");
        let leaf_cert = leaf_params
            .signed_by(&leaf_key, &ca_cert, &ca_key)
            .expect("leaf cert");

        let material = TlsMaterial::from_pem(
            leaf_cert.pem().as_bytes(),
            leaf_key.serialize_pem().as_bytes(),
        )
        .expect("material");
        let server_config = material
            .into_server_config(router_alpn())
            .expect("server config");
        let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(server_config));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let addr = listener.local_addr().expect("local addr");

        let (shutdown_tx, mut shutdown_rx) = tokio::sync::watch::channel(false);
        let shutdown = async move {
            while !*shutdown_rx.borrow_and_update() {
                if shutdown_rx.changed().await.is_err() {
                    break;
                }
            }
        };
        let server = tokio::spawn(
            Server::builder()
                .layer(MirrorGzipLayer)
                .add_service(
                    RouterService::with_static_key(b"wire-test-router-key".to_vec()).into_server(),
                )
                .add_service(reflection_builder().build_v1().expect("reflection v1"))
                .add_service(
                    reflection_builder()
                        .build_v1alpha()
                        .expect("reflection v1alpha"),
                )
                .serve_with_incoming_shutdown(tls_incoming(listener, acceptor), shutdown),
        );

        let tls = tonic::transport::ClientTlsConfig::new()
            .ca_certificate(tonic::transport::Certificate::from_pem(ca_cert.pem()))
            .domain_name("localhost");
        let channel = tonic::transport::Channel::from_shared(format!("https://{addr}"))
            .expect("uri")
            .tls_config(tls)
            .expect("tls config")
            .connect()
            .await
            .expect("TLS connect (h2-only ALPN)");

        // Reflection lists exactly the Go router's surface: RouterService +
        // reflection, and crucially NO grpc.health.v1.Health.
        let mut reflection = ServerReflectionClient::new(channel.clone());
        let request = ServerReflectionRequest {
            host: String::new(),
            message_request: Some(MessageRequest::ListServices(String::new())),
        };
        let mut responses = reflection
            .server_reflection_info(tokio_stream::once(request))
            .await
            .expect("reflection stream")
            .into_inner();
        let response = responses
            .message()
            .await
            .expect("reflection response")
            .expect("non-empty reflection response");
        let Some(MessageResponse::ListServicesResponse(list)) = response.message_response else {
            panic!("expected ListServicesResponse, got {response:?}");
        };
        let mut names: Vec<_> = list.service.into_iter().map(|s| s.name).collect();
        names.sort();
        assert_eq!(
            names,
            vec![
                "grpc.reflection.v1.ServerReflection",
                "grpc.reflection.v1alpha.ServerReflection",
                "jumpstarter.v1.RouterService",
            ]
        );

        // The production service admits streams only with a bearer token:
        // an unauthenticated Stream is rejected with the Go bearer status
        // (bearer.go:40-42).
        let mut router = pb::router_service_client::RouterServiceClient::new(channel.clone());
        let status = router
            .stream(tokio_stream::empty::<pb::StreamRequest>())
            .await
            .expect_err("unauthenticated stream must be rejected");
        assert_eq!(status.code(), tonic::Code::Unauthenticated);
        assert_eq!(status.message(), "missing authorization header");

        drop(channel);
        shutdown_tx.send(true).expect("signal shutdown");
        server
            .await
            .expect("server task join")
            .expect("clean server shutdown");
    }
}
