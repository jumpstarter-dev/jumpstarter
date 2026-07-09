//! The **black-box gRPC differential** leg of the conformance harness.
//!
//! Unlike [`crate::cases`], which drives the Rust services in-process (and
//! reaches into white-box internals like the `ListenRegistry`), this module
//! runs a subset of cases that are expressible as *pure wire behavior* —
//! arrange CR state via kube, then issue **one** gRPC call over a real
//! `tonic::transport::Channel` and observe the resulting `(code, message)`.
//! Because the observation is transport-level, the identical case set can run
//! against BOTH controllers:
//!
//!  - the Rust services, served over a local tonic port ([`serve_rust`]);
//!  - the real Go controller, via the `controller/hack/conformance` server
//!    spawned against the SAME envtest apiserver with the SAME signing key.
//!
//! [`EXPECTED`] is the source-of-truth contract table (spec 02 §12). The
//! differential test asserts every observed [`Outcome`] matches it AND that the
//! Go and Rust observations agree; the committed goldens under `tests/golden/`
//! record the Go-observed values, and a non-env-gated replay test re-checks
//! those goldens against [`EXPECTED`] with no cluster.
//!
//! Error strings that embed a per-run identifier (the soft-delete lease name's
//! UUID, the expired-token detail's timestamp) are matched as **substrings**
//! (`Expected::substring`) — the contractual part is the substring, not the
//! byte-identical whole (spec 02 §12.2). The apiserver "not found" text is NOT
//! one of these: its lease name ("ghost") is a fixed literal, so that row is
//! pinned byte-exact to hold the verbatim passthrough (spec 02 §12.2 wart #2).

#![allow(clippy::result_large_err)]

use std::net::SocketAddr;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::oneshot;
use tokio_stream::wrappers::TcpListenerStream;
use tonic::transport::{Channel, Server};

use jumpstarter_protocol::client_v1 as cpb;
use jumpstarter_protocol::client_v1::client_service_client::ClientServiceClient;
use jumpstarter_protocol::client_v1::client_service_server::ClientServiceServer;
use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::controller_service_server::ControllerServiceServer;

use crate::harness::{request, request_with, Harness, TestEnv, ROUTER_ENDPOINT, SIGNER_SEED};

/// One observed RPC result: the gRPC status code name and its message. Success
/// is recorded as `{ code: "Ok", message: "" }`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Outcome {
    pub code: String,
    pub message: String,
}

impl Outcome {
    fn ok() -> Self {
        Outcome {
            code: "Ok".to_string(),
            message: String::new(),
        }
    }
    fn from<T>(res: Result<tonic::Response<T>, tonic::Status>) -> Self {
        match res {
            Ok(_) => Outcome::ok(),
            Err(s) => Outcome {
                code: format!("{:?}", s.code()),
                message: s.message().to_string(),
            },
        }
    }
}

/// A single contract row: the code every implementation must return, and the
/// message either verbatim (`substring == false`) or as a required substring
/// (`substring == true`, for messages carrying a per-run identifier).
pub struct Expected {
    pub name: &'static str,
    pub code: &'static str,
    pub message: &'static str,
    pub substring: bool,
}

impl Expected {
    /// Does `got` satisfy this contract row?
    pub fn matches(&self, got: &Outcome) -> Result<(), String> {
        if got.code != self.code {
            return Err(format!("code {:?} != expected {:?}", got.code, self.code));
        }
        let ok = if self.substring {
            got.message.contains(self.message)
        } else {
            got.message == self.message
        };
        if !ok {
            let how = if self.substring { "contain" } else { "equal" };
            return Err(format!(
                "message {:?} does not {how} expected {:?}",
                got.message, self.message
            ));
        }
        Ok(())
    }
}

/// The spec-02 §12 error-string contract as a machine-checkable table. Every
/// name here has a matching arm in [`observe`]; the differential test drives
/// both, and the golden replay test checks the recorded Go values against it.
pub const EXPECTED: &[Expected] = &[
    // -- Dial error contract ------------------------------------------------
    Expected { name: "empty_lease_name", code: "Unknown", message: "empty lease name", substring: false },
    Expected { name: "permission_denied", code: "Unknown", message: "permission denied", substring: false },
    Expected { name: "lease_not_active", code: "Unknown", message: "lease not active", substring: false },
    Expected { name: "exporter_not_listening", code: "Unavailable", message: "exporter is not listening on lease lease", substring: false },
    // Verbatim apiserver not-found text, never remapped to NOT_FOUND. The lease
    // name "ghost" is a fixed literal (no per-run identifier), so the whole
    // apiserver string is deterministic: pin it byte-exact (substring: false) so
    // Rust is compared full-message-identical to the Go-observed apiserver text.
    Expected { name: "apiserver_notfound_verbatim", code: "Unknown", message: "leases.jumpstarter.dev \"ghost\" not found", substring: false },
    // -- auth / metadata contract -------------------------------------------
    Expected { name: "missing_authorization_header", code: "Unauthenticated", message: "missing authorization header", substring: false },
    Expected { name: "malformed_header", code: "InvalidArgument", message: "malformed authorization header", substring: false },
    Expected { name: "multiple_headers", code: "InvalidArgument", message: "multiple authorization headers", substring: false },
    Expected { name: "object_kind_mismatch", code: "InvalidArgument", message: "object kind mismatch", substring: false },
    Expected { name: "missing_namespace_metadata", code: "InvalidArgument", message: "missing metadata: jumpstarter-namespace", substring: false },
    Expected { name: "resource_name_required", code: "InvalidArgument", message: "resource name required for pre-existing authentication", substring: false },
    // Expired internal token: UNKNOWN carrying the re-auth trigger substring.
    Expected { name: "token_expired", code: "Unknown", message: "token is expired", substring: true },
    // -- ClientService contract ---------------------------------------------
    Expected { name: "invalid_argument_create_lease", code: "InvalidArgument", message: "one of selector or exporter_name is required", substring: false },
    Expected { name: "namespace_mismatch", code: "PermissionDenied", message: "namespace mismatch", substring: false },
    Expected { name: "aip_bad_segments", code: "InvalidArgument", message: "invalid number of segments in identifier \"namespaces/ns/leases\", expecting 4, got 3", substring: false },
    Expected { name: "aip_bad_first", code: "InvalidArgument", message: "invalid first segment in identifier \"nope/ns/leases/l\", expecting \"namespaces\", got \"nope\"", substring: false },
    Expected { name: "aip_bad_third", code: "InvalidArgument", message: "invalid third segment in identifier \"namespaces/ns/pods/l\", expecting \"leases\", got \"pods\"", substring: false },
    // Idempotent soft-delete: message embeds the lease AIP name (per-run UUID).
    Expected { name: "soft_delete_idempotent", code: "FailedPrecondition", message: "has already been released", substring: true },
    // -- success paths ------------------------------------------------------
    Expected { name: "register_ok", code: "Ok", message: "", substring: false },
    Expected { name: "create_lease_ok", code: "Ok", message: "", substring: false },
];

// ===========================================================================
// golden file (recorded Go behavior, replayed without a cluster)
// ===========================================================================

/// One recorded case observation in a golden file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GoldenCase {
    pub name: String,
    pub code: String,
    pub message: String,
}

/// The committed record of one implementation's observed `(code, message)` per
/// case. `source` is `"go"` when captured from the real Go controller, or
/// `"rust-provisional"` when the Go leg could not be brought up (a TODO to
/// re-record against Go once available).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Golden {
    #[serde(rename = "_comment")]
    pub comment: String,
    pub source: String,
    pub cases: Vec<GoldenCase>,
}

impl Golden {
    pub fn from_outcomes(
        source: &str,
        comment: &str,
        outcomes: &[(&'static str, Outcome)],
    ) -> Self {
        Golden {
            comment: comment.to_string(),
            source: source.to_string(),
            cases: outcomes
                .iter()
                .map(|(name, o)| GoldenCase {
                    name: name.to_string(),
                    code: o.code.clone(),
                    message: o.message.clone(),
                })
                .collect(),
        }
    }

    pub fn lookup(&self, name: &str) -> Option<Outcome> {
        self.cases.iter().find(|c| c.name == name).map(|c| Outcome {
            code: c.code.clone(),
            message: c.message.clone(),
        })
    }
}

/// Path to the committed golden file (`tests/golden/go_controller.json`).
pub fn golden_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/golden/go_controller.json")
}

fn controller(ch: &Channel) -> ControllerServiceClient<Channel> {
    ControllerServiceClient::new(ch.clone())
}
fn client(ch: &Channel) -> ClientServiceClient<Channel> {
    ClientServiceClient::new(ch.clone())
}

/// Arrange the CR state a case needs (via kube) and issue its single gRPC call
/// over `ch`, returning the observed [`Outcome`]. `ns` is a per-(endpoint,case)
/// namespace so the Go and Rust legs never collide on the shared apiserver.
///
/// Returns `Err` only on *harness* failure (CR arrangement / token mint); the
/// RPC's own status — success or error — is the [`Outcome`] we compare.
pub async fn observe(name: &str, ch: &Channel, h: &Harness, ns: &str) -> Result<Outcome, String> {
    let out = match name {
        "empty_lease_name" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(
                pb::DialRequest {
                    lease_name: String::new(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(controller(ch).dial(req).await)
        }
        "permission_denied" => {
            h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
            let (_c, token) = h.make_client(ns, "cli").await?;
            h.set_exporter_status(ns, "exp", "LeaseReady").await?;
            h.create_assigned_lease(ns, "lease", "other-client", "exp", &[("dut", "a")])
                .await?;
            let req = request(
                pb::DialRequest {
                    lease_name: "lease".into(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(controller(ch).dial(req).await)
        }
        "lease_not_active" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            h.create_unassigned_lease(ns, "lease", "cli").await?;
            let req = request(
                pb::DialRequest {
                    lease_name: "lease".into(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(controller(ch).dial(req).await)
        }
        "exporter_not_listening" => {
            h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
            let (_c, token) = h.make_client(ns, "cli").await?;
            h.set_exporter_status(ns, "exp", "LeaseReady").await?;
            h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
                .await?;
            let req = request(
                pb::DialRequest {
                    lease_name: "lease".into(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(controller(ch).dial(req).await)
        }
        "apiserver_notfound_verbatim" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(
                pb::DialRequest {
                    lease_name: "ghost".into(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(controller(ch).dial(req).await)
        }
        "missing_authorization_header" => {
            let req = request_with(pb::RegisterRequest::default(), |md| {
                md.insert("jumpstarter-namespace", ns.parse().unwrap());
                md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
                md.insert("jumpstarter-name", "exp".parse().unwrap());
            });
            Outcome::from(controller(ch).register(req).await)
        }
        "malformed_header" => {
            let req = request_with(pb::RegisterRequest::default(), |md| {
                md.insert("authorization", "Basic Zm9v".parse().unwrap());
                md.insert("jumpstarter-namespace", ns.parse().unwrap());
                md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
                md.insert("jumpstarter-name", "exp".parse().unwrap());
            });
            Outcome::from(controller(ch).register(req).await)
        }
        "multiple_headers" => {
            let req = request_with(pb::RegisterRequest::default(), |md| {
                md.append("authorization", "Bearer a".parse().unwrap());
                md.append("authorization", "Bearer b".parse().unwrap());
                md.insert("jumpstarter-namespace", ns.parse().unwrap());
                md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
                md.insert("jumpstarter-name", "exp".parse().unwrap());
            });
            Outcome::from(controller(ch).register(req).await)
        }
        "object_kind_mismatch" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(pb::RegisterRequest::default(), &token, ns, "Client", "cli");
            Outcome::from(controller(ch).register(req).await)
        }
        "missing_namespace_metadata" => {
            let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
            let req = request_with(pb::RegisterRequest::default(), |md| {
                md.insert("authorization", format!("Bearer {token}").parse().unwrap());
                md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
                md.insert("jumpstarter-name", "exp".parse().unwrap());
            });
            Outcome::from(controller(ch).register(req).await)
        }
        "resource_name_required" => {
            let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
            let req = request_with(pb::RegisterRequest::default(), |md| {
                md.insert("authorization", format!("Bearer {token}").parse().unwrap());
                md.insert("jumpstarter-namespace", ns.parse().unwrap());
                md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
                // no jumpstarter-name
            });
            Outcome::from(controller(ch).register(req).await)
        }
        "token_expired" => {
            let (exp, _valid) = h.make_exporter(ns, "exp", &[]).await?;
            let expired = h.expired_token(&exp.internal_subject())?;
            let req = request(
                pb::RegisterRequest::default(),
                &expired,
                ns,
                "Exporter",
                "exp",
            );
            Outcome::from(controller(ch).register(req).await)
        }
        "invalid_argument_create_lease" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(
                cpb::CreateLeaseRequest {
                    parent: format!("namespaces/{ns}"),
                    lease_id: String::new(),
                    lease: Some(cpb::Lease {
                        selector: String::new(),
                        exporter_name: None,
                        ..Default::default()
                    }),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(client(ch).create_lease(req).await)
        }
        "namespace_mismatch" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(
                cpb::CreateLeaseRequest {
                    parent: "namespaces/other-namespace".into(),
                    lease_id: String::new(),
                    lease: Some(cpb::Lease {
                        selector: "dut=a".into(),
                        ..Default::default()
                    }),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(client(ch).create_lease(req).await)
        }
        "aip_bad_segments" | "aip_bad_first" | "aip_bad_third" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let bad = match name {
                "aip_bad_segments" => "namespaces/ns/leases",
                "aip_bad_first" => "nope/ns/leases/l",
                _ => "namespaces/ns/pods/l",
            };
            let req = request(
                cpb::GetLeaseRequest { name: bad.into() },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(client(ch).get_lease(req).await)
        }
        "soft_delete_idempotent" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let created = client(ch)
                .create_lease(request(
                    cpb::CreateLeaseRequest {
                        parent: format!("namespaces/{ns}"),
                        lease_id: String::new(),
                        lease: Some(cpb::Lease {
                            selector: "dut=a".into(),
                            duration: Some(prost_types::Duration {
                                seconds: 60,
                                nanos: 0,
                            }),
                            ..Default::default()
                        }),
                    },
                    &token,
                    ns,
                    "Client",
                    "cli",
                ))
                .await
                .map_err(|s| format!("create_lease for soft-delete: {s}"))?
                .into_inner();
            client(ch)
                .delete_lease(request(
                    cpb::DeleteLeaseRequest {
                        name: created.name.clone(),
                    },
                    &token,
                    ns,
                    "Client",
                    "cli",
                ))
                .await
                .map_err(|s| format!("first delete_lease: {s}"))?;
            // Second delete → FAILED_PRECONDITION "... has already been released".
            let req = request(
                cpb::DeleteLeaseRequest {
                    name: created.name.clone(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(client(ch).delete_lease(req).await)
        }
        "register_ok" => {
            let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
            let req = request(
                pb::RegisterRequest::default(),
                &token,
                ns,
                "Exporter",
                "exp",
            );
            Outcome::from(controller(ch).register(req).await)
        }
        "create_lease_ok" => {
            let (_c, token) = h.make_client(ns, "cli").await?;
            let req = request(
                cpb::CreateLeaseRequest {
                    parent: format!("namespaces/{ns}"),
                    lease_id: String::new(),
                    lease: Some(cpb::Lease {
                        selector: "dut=a".into(),
                        duration: Some(prost_types::Duration {
                            seconds: 60,
                            nanos: 0,
                        }),
                        ..Default::default()
                    }),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            Outcome::from(client(ch).create_lease(req).await)
        }
        other => return Err(format!("unknown black-box case {other:?}")),
    };
    Ok(out)
}

/// Run the whole [`EXPECTED`] set against one endpoint, a fresh namespace per
/// case (named `{prefix}NN`), returning `(name, Outcome)` in table order.
pub async fn run_endpoint(
    label: &str,
    ch: &Channel,
    env: &TestEnv,
    h: &Harness,
    prefix: &str,
) -> Vec<(&'static str, Result<Outcome, String>)> {
    let mut out = Vec::with_capacity(EXPECTED.len());
    for (i, e) in EXPECTED.iter().enumerate() {
        let ns = format!("{prefix}{i:02}");
        let r = match env.create_namespace(&ns).await {
            Ok(()) => observe(e.name, ch, h, &ns).await,
            Err(err) => Err(format!("create namespace {ns}: {err}")),
        };
        match &r {
            Ok(o) => eprintln!("[{label}] {} -> {}: {:?}", e.name, o.code, o.message),
            Err(err) => eprintln!("[{label}] {} -> HARNESS ERROR: {err}", e.name),
        }
        out.push((e.name, r));
    }
    out
}

// ===========================================================================
// serving the Rust services over a real tonic port
// ===========================================================================

/// A locally-served Rust `ControllerService` + `ClientService` on a real gRPC
/// port. Dropping it (or calling [`RustServer::shutdown`]) stops the server.
pub struct RustServer {
    pub addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    handle: tokio::task::JoinHandle<()>,
}

impl RustServer {
    pub async fn shutdown(mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        let _ = self.handle.await;
    }
}

/// Serve the harness's real services on `127.0.0.1:0` and return the bound
/// address plus a shutdown handle. The generated servers wrap the same `Arc`
/// the in-process suite uses, so metadata-driven auth is exercised identically
/// — only the TLS/HTTP2 framing is added.
pub async fn serve_rust(h: &Harness) -> Result<RustServer, String> {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|e| format!("bind rust server: {e}"))?;
    let addr = listener.local_addr().map_err(|e| e.to_string())?;
    let incoming = TcpListenerStream::new(listener);
    let (tx, rx) = oneshot::channel::<()>();

    let ctrl = ControllerServiceServer::from_arc(h.controller.clone());
    let cli = ClientServiceServer::from_arc(h.client_svc.clone());

    let handle = tokio::spawn(async move {
        let _ = Server::builder()
            .add_service(ctrl)
            .add_service(cli)
            .serve_with_incoming_shutdown(incoming, async {
                let _ = rx.await;
            })
            .await;
    });

    Ok(RustServer {
        addr,
        shutdown: Some(tx),
        handle,
    })
}

/// Connect a plaintext `Channel` to `addr`, bounded.
pub async fn connect(addr: SocketAddr) -> Result<Channel, String> {
    let uri = format!("http://{addr}");
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        match Channel::from_shared(uri.clone())
            .map_err(|e| format!("bad uri {uri}: {e}"))?
            .connect_timeout(Duration::from_secs(2))
            .connect()
            .await
        {
            Ok(ch) => return Ok(ch),
            Err(e) if Instant::now() >= deadline => {
                return Err(format!("connect {uri}: {e}"));
            }
            Err(_) => tokio::time::sleep(Duration::from_millis(100)).await,
        }
    }
}

// ===========================================================================
// spawning the Go conformance server
// ===========================================================================

/// A running Go conformance server subprocess plus the address it serves on.
pub struct GoServer {
    pub addr: SocketAddr,
    child: Child,
}

impl GoServer {
    /// Best-effort kill on teardown.
    pub async fn stop(mut self) {
        let _ = self.child.start_kill();
        let _ = self.child.wait().await;
    }
}

/// Spawn `bin` (the built `controller/hack/conformance` server) against the
/// SAME envtest apiserver (`kubeconfig`) with the SAME fixed keys, and wait
/// (bounded) for its `CONFORMANCE-SERVER-READY <addr>` stdout line. ANTI-STALL:
/// a Go server that never becomes ready surfaces as an `Err` within `budget`,
/// never a hang.
pub async fn spawn_go(
    bin: &str,
    kubeconfig: &std::path::Path,
    grpc_port: u16,
    log_path: &std::path::Path,
    budget: Duration,
) -> Result<GoServer, String> {
    let stderr_log = std::fs::File::create(log_path)
        .map_err(|e| format!("create go log {}: {e}", log_path.display()))?;
    let mut child = Command::new(bin)
        .arg("-grpc-addr")
        .arg(format!("127.0.0.1:{grpc_port}"))
        .arg("-router-endpoint")
        .arg(ROUTER_ENDPOINT)
        .env("KUBECONFIG", kubeconfig)
        .env(
            "CONTROLLER_KEY",
            String::from_utf8_lossy(SIGNER_SEED).into_owned(),
        )
        .env(
            "ROUTER_KEY",
            String::from_utf8_lossy(crate::harness::ROUTER_KEY).into_owned(),
        )
        .stdout(std::process::Stdio::piped())
        .stderr(stderr_log)
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("spawn go server {bin}: {e}"))?;

    let stdout = child.stdout.take().ok_or("go server: no stdout pipe")?;
    let mut lines = BufReader::new(stdout).lines();

    let ready = tokio::time::timeout(budget, async {
        while let Ok(Some(line)) = lines.next_line().await {
            eprintln!("[go] {line}");
            if let Some(rest) = line.strip_prefix("CONFORMANCE-SERVER-READY ") {
                return Some(rest.trim().to_string());
            }
        }
        None
    })
    .await;

    let addr_str = match ready {
        Ok(Some(a)) => a,
        Ok(None) => {
            let _ = child.start_kill();
            let tail = std::fs::read_to_string(log_path).unwrap_or_default();
            let tail: String = tail.lines().rev().take(30).collect::<Vec<_>>().join("\n");
            return Err(format!(
                "go server exited before READY. stderr tail:\n{tail}"
            ));
        }
        Err(_) => {
            let _ = child.start_kill();
            let tail = std::fs::read_to_string(log_path).unwrap_or_default();
            let tail: String = tail.lines().rev().take(30).collect::<Vec<_>>().join("\n");
            return Err(format!(
                "go server not ready within {budget:?}. stderr tail:\n{tail}"
            ));
        }
    };

    // Keep draining stdout so the pipe never blocks the child.
    tokio::spawn(async move {
        while let Ok(Some(line)) = lines.next_line().await {
            eprintln!("[go] {line}");
        }
    });

    let addr: SocketAddr = addr_str
        .parse()
        .map_err(|e| format!("parse go server addr {addr_str:?}: {e}"))?;
    Ok(GoServer { addr, child })
}
