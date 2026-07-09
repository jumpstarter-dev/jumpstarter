//! Go-vs-Rust differential harness for the router (Phase 2 of the controller
//! rewrite).
//!
//! Two entry points:
//!
//! - [`differential_go_vs_rust`] — env-gated: when **both** `JMP_GO_ROUTER_BIN`
//!   and `JMP_RUST_ROUTER_BIN` are set, spawns each binary in turn on `:8083`
//!   (both binaries hard-code the port: `net.Listen("tcp", ":8083")` in
//!   `controller/internal/service/router_service.go:164` and
//!   `GRPC_LISTEN_ADDR` in `rust/jumpstarter-router/src/main.rs` — there is
//!   **no** port override, so the harness requires 8083 to be free and runs
//!   the two binaries sequentially), runs the scenario suite against each,
//!   records the Go observations to `tests/golden/router_behavior.json`, and
//!   fails on ANY divergence in status code/message or frame-level behavior.
//!
//! - [`rust_router_matches_recorded_go_goldens`] — always runs: replays the
//!   scenario suite against an in-process Rust router served over TLS (the
//!   same tonic stack the `jumpstarter-router` binary assembles) and diffs it
//!   against the committed Go goldens, so CI without a Go toolchain still
//!   enforces parity.
//!
//! ## Standalone-router environment (what the binaries need)
//!
//! Both binaries build a Kubernetes client and fatally load the `config` key
//! of the `jumpstarter-controller` ConfigMap in `$NAMESPACE`
//! (`controller/cmd/router/main.go:61-75`) — an absent ConfigMap (or unset
//! `NAMESPACE`, or unreachable apiserver) is exit 1 "failed to load router
//! configuration"; nothing is defaulted. The harness therefore runs a minimal
//! fake apiserver (legacy JSON discovery + that one ConfigMap) and points
//! `KUBECONFIG` at it. TLS: `EXTERNAL_CERT_PEM`/`EXTERNAL_KEY_PEM` file paths
//! (both set ⇒ used verbatim; else self-signed) — the harness provisions an
//! rcgen CA + `localhost` leaf so the client can pin a real trust anchor.
//! `ROUTER_KEY` is the raw HS256 HMAC key, re-read per authentication.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use jsonwebtoken::{Algorithm, EncodingKey, Header};
use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::transport::{Certificate, Channel, ClientTlsConfig};
use tonic::Request;

const ROUTER_KEY: &str = "differential-router-key";
const ROUTER_PORT: u16 = 8083;
const GOLDEN_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/golden/router_behavior.json"
);

/// How long scenario (g) watches a lone waiter for a router-initiated
/// timeout. Spec 06 §3.2 documents **no** router timers; ~65 s comfortably
/// covers any hidden 30/60 s timer. Override with
/// `JMP_ROUTER_LONE_WAIT_SECS` while iterating locally — goldens MUST be
/// recorded (and CI replayed) at the default.
fn lone_waiter_window() -> Duration {
    let secs = std::env::var("JMP_ROUTER_LONE_WAIT_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(65);
    Duration::from_secs(secs)
}

// ---------------------------------------------------------------------------
// Observation model (serialized as the golden record)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct FrameObs {
    frame_type: i32,
    payload_b64: String,
}

impl FrameObs {
    fn of(frame: &pb::StreamResponse) -> Self {
        Self {
            frame_type: frame.frame_type,
            payload_b64: base64::engine::general_purpose::STANDARD.encode(&frame.payload),
        }
    }
}

/// How a peer's `Stream` RPC ended, as observed by the client.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum EndObs {
    /// Clean end of the response stream (OK trailers).
    Ok,
    /// Terminal gRPC status — code name and VERBATIM message.
    Status { code: String, message: String },
    /// No terminal event within the scenario's observation window.
    StillOpen,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct PeerObs {
    /// Every frame this peer received, in order, verbatim.
    frames: Vec<FrameObs>,
    end: EndObs,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct ScenarioObservation {
    scenario: String,
    /// Keyed by a stable role name (e.g. "waiter", "pairer", "survivor").
    peers: BTreeMap<String, PeerObs>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct GoldenFile {
    /// Provenance note for humans reading the JSON.
    description: String,
    scenarios: Vec<ScenarioObservation>,
}

// ---------------------------------------------------------------------------
// Token minting (Dial-shaped router stream tokens, spec 02 §6.2)
// ---------------------------------------------------------------------------

fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

fn mint_claims(key: &[u8], claims: serde_json::Value) -> String {
    jsonwebtoken::encode(
        &Header::new(Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(key),
    )
    .expect("encode token")
}

fn mint(sub: &str) -> String {
    let now = unix_now();
    mint_claims(
        ROUTER_KEY.as_bytes(),
        serde_json::json!({
            "iss": "https://jumpstarter.dev/stream",
            "sub": sub,
            "aud": ["https://jumpstarter.dev/router"],
            "exp": now + 1800,
            "nbf": now,
            "iat": now,
            "jti": format!("jti-{sub}"),
        }),
    )
}

// ---------------------------------------------------------------------------
// Peer: a Stream RPC that never blocks on response headers
// ---------------------------------------------------------------------------
//
// grpc-go does not send response HEADERS until the handler's first Send, so a
// parked waiter's call produces nothing until pairing — the harness must not
// await headers before driving the scenario. Each peer runs its call in a
// task that forwards frames/terminal events over a channel.

#[derive(Debug)]
enum PeerEvent {
    Frame(pb::StreamResponse),
    End(EndObs),
}

struct Peer {
    tx: Option<mpsc::Sender<pb::StreamRequest>>,
    events: mpsc::UnboundedReceiver<PeerEvent>,
    task: tokio::task::JoinHandle<()>,
    /// Kept so aborting `task` cancels only the RPC (h2 RST_STREAM), not the
    /// whole connection.
    _channel: Channel,
    seen: Vec<FrameObs>,
    ended: Option<EndObs>,
}

impl Peer {
    /// Send a frame; ignores failures (a dead stream is an observation, not
    /// a harness error).
    async fn send(&self, payload: &[u8], frame_type: i32) {
        if let Some(tx) = &self.tx {
            let _ = tx
                .send(pb::StreamRequest {
                    payload: payload.to_vec(),
                    frame_type,
                })
                .await;
        }
        // Yield so the frame hits the wire before the caller's next step.
        tokio::time::sleep(Duration::from_millis(20)).await;
    }

    /// Half-close the send direction (client `done_writing`).
    fn half_close(&mut self) {
        self.tx = None;
    }

    /// Abort the RPC abruptly (h2 RST_STREAM CANCEL — a client cancelling the
    /// call, e.g. the Python exporter tearing down mid-forward).
    fn hard_cancel(self) {
        self.task.abort();
    }

    /// Wait until `n` more frames arrived (or the stream ended / `timeout`
    /// passed — both are recorded, not harness failures).
    async fn expect_frames(&mut self, n: usize, timeout: Duration) {
        let deadline = tokio::time::Instant::now() + timeout;
        let mut remaining = n;
        while remaining > 0 && self.ended.is_none() {
            match tokio::time::timeout_at(deadline, self.events.recv()).await {
                Err(_) => return, // timed out; the diff will surface it
                Ok(None) => return,
                Ok(Some(PeerEvent::Frame(frame))) => {
                    self.seen.push(FrameObs::of(&frame));
                    remaining -= 1;
                }
                Ok(Some(PeerEvent::End(end))) => self.ended = Some(end),
            }
        }
    }

    /// Drain until the RPC ends (or `window` passes ⇒ `StillOpen`) and return
    /// the full observation.
    async fn finish(mut self, window: Duration) -> PeerObs {
        let deadline = tokio::time::Instant::now() + window;
        while self.ended.is_none() {
            match tokio::time::timeout_at(deadline, self.events.recv()).await {
                Err(_) => self.ended = Some(EndObs::StillOpen),
                Ok(None) => self.ended = Some(EndObs::StillOpen),
                Ok(Some(PeerEvent::Frame(frame))) => self.seen.push(FrameObs::of(&frame)),
                Ok(Some(PeerEvent::End(end))) => self.ended = Some(end),
            }
        }
        PeerObs {
            frames: self.seen,
            end: self.ended.unwrap(),
        }
    }
}

const FINISH: Duration = Duration::from_secs(10);

fn end_of(status: &tonic::Status) -> EndObs {
    EndObs::Status {
        code: format!("{:?}", status.code()),
        message: status.message().to_string(),
    }
}

/// Opens a `Stream` RPC over an already-connected channel without waiting
/// for response headers.
fn open_on(channel: Channel, token: Option<&str>) -> Peer {
    let (tx, rx) = mpsc::channel::<pb::StreamRequest>(64);
    let (event_tx, events) = mpsc::unbounded_channel();
    let mut request = Request::new(ReceiverStream::new(rx));
    if let Some(token) = token {
        request.metadata_mut().insert(
            "authorization",
            format!("Bearer {token}").parse().expect("metadata value"),
        );
    }
    let call_channel = channel.clone();
    let task = tokio::spawn(async move {
        let mut client = RouterServiceClient::new(call_channel);
        match client.stream(request).await {
            Err(status) => {
                let _ = event_tx.send(PeerEvent::End(end_of(&status)));
            }
            Ok(response) => {
                let mut inbound = response.into_inner();
                loop {
                    match inbound.message().await {
                        Ok(Some(frame)) => {
                            let _ = event_tx.send(PeerEvent::Frame(frame));
                        }
                        Ok(None) => {
                            let _ = event_tx.send(PeerEvent::End(EndObs::Ok));
                            break;
                        }
                        Err(status) => {
                            let _ = event_tx.send(PeerEvent::End(end_of(&status)));
                            break;
                        }
                    }
                }
            }
        }
    });
    Peer {
        tx: Some(tx),
        events,
        task,
        _channel: channel,
        seen: Vec::new(),
        ended: None,
    }
}

// ---------------------------------------------------------------------------
// Target: one running router (binary on :8083 or in-process)
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct Target {
    addr: std::net::SocketAddr,
    ca_pem: String,
}

impl Target {
    async fn channel_to(&self, addr: std::net::SocketAddr) -> Channel {
        Channel::from_shared(format!("https://localhost:{}", addr.port()))
            .expect("uri")
            .tls_config(
                ClientTlsConfig::new()
                    .ca_certificate(Certificate::from_pem(self.ca_pem.clone()))
                    .domain_name("localhost"),
            )
            .expect("client tls")
            .connect()
            .await
            .expect("TLS connect to router")
    }

    async fn open(&self, token: Option<&str>) -> Peer {
        open_on(self.channel_to(self.addr).await, token)
    }

    /// Open a peer through a single-connection TCP relay whose transport can
    /// be severed on command — a peer process dying without any clean h2
    /// stream close.
    async fn open_severable(&self, token: &str) -> (Peer, tokio::sync::oneshot::Sender<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind proxy");
        let proxy_addr = listener.local_addr().expect("proxy addr");
        let target = self.addr;
        let (kill_tx, kill_rx) = tokio::sync::oneshot::channel::<()>();
        tokio::spawn(async move {
            let (mut inbound, _) = listener.accept().await.expect("proxy accept");
            let mut outbound = tokio::net::TcpStream::connect(target)
                .await
                .expect("proxy connect");
            tokio::select! {
                _ = kill_rx => { /* drop both sockets: transport dies */ }
                _ = tokio::io::copy_bidirectional(&mut inbound, &mut outbound) => {}
            }
        });
        let peer = open_on(self.channel_to(proxy_addr).await, Some(token));
        (peer, kill_tx)
    }
}

/// Give the router time to park the first peer before the second connects
/// (there is no external signal for "entry stored").
async fn settle() {
    tokio::time::sleep(Duration::from_millis(500)).await;
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

async fn run_scenarios(target: &Target) -> Vec<ScenarioObservation> {
    let started = std::time::Instant::now();
    let mut all: Vec<ScenarioObservation> = Vec::new();
    // Progress print after every scenario so long runs are never silent.
    macro_rules! record {
        ($fut:expr) => {{
            let obs = $fut.await;
            eprintln!(
                "[differential +{:>6.1}s] scenario {} done",
                started.elapsed().as_secs_f64(),
                obs.scenario
            );
            all.push(obs);
        }};
    }
    record!(scenario_echo_clean_close(
        target,
        "a_echo_clean_close",
        "sub-a"
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b1_waiter_rst_cancel",
        "sub-b1",
        Dead::Waiter,
        Kill::Rst,
        Nudge::No,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b2_pairer_rst_cancel",
        "sub-b2",
        Dead::Pairer,
        Kill::Rst,
        Nudge::No,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b3_waiter_conn_sever",
        "sub-b3",
        Dead::Waiter,
        Kill::Sever,
        Nudge::No,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b4_pairer_conn_sever",
        "sub-b4",
        Dead::Pairer,
        Kill::Sever,
        Nudge::No,
    ));
    // The `n` variants make the deferred first error wire-visible: after the
    // kill, the survivor sends a frame; its relay to the dead peer fails, so
    // the survivor's own pipe ends, `Forward` returns, and the survivor
    // finally observes its terminal event — the status text the Python
    // exporter retry classifier reads.
    record!(scenario_cancel_mid_forward(
        target,
        "b1n_waiter_rst_cancel_survivor_nudge",
        "sub-b1n",
        Dead::Waiter,
        Kill::Rst,
        Nudge::Yes,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b2n_pairer_rst_cancel_survivor_nudge",
        "sub-b2n",
        Dead::Pairer,
        Kill::Rst,
        Nudge::Yes,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b3n_waiter_conn_sever_survivor_nudge",
        "sub-b3n",
        Dead::Waiter,
        Kill::Sever,
        Nudge::Yes,
    ));
    record!(scenario_cancel_mid_forward(
        target,
        "b4n_pairer_conn_sever_survivor_nudge",
        "sub-b4n",
        Dead::Pairer,
        Kill::Sever,
        Nudge::Yes,
    ));
    record!(scenario_pre_pairing_cancel_reconnect(target));
    record!(scenario_goaway_half_close(target));
    for obs in scenario_bad_tokens(target).await {
        eprintln!(
            "[differential +{:>6.1}s] scenario {} done",
            started.elapsed().as_secs_f64(),
            obs.scenario
        );
        all.push(obs);
    }
    record!(scenario_token_reuse_after_pair(target));
    record!(scenario_frames_verbatim(target));
    // Last: the long lone-waiter watch (bounded by lone_waiter_window()).
    eprintln!(
        "[differential +{:>6.1}s] scenario g_lone_waiter_no_timeout: observing a lone \
         waiter for {:?}...",
        started.elapsed().as_secs_f64(),
        lone_waiter_window()
    );
    record!(scenario_lone_waiter(target));
    all
}

/// (a) Both peers connect, bidirectional DATA echo, clean close.
async fn scenario_echo_clean_close(target: &Target, name: &str, sub: &str) -> ScenarioObservation {
    let token = mint(sub);
    let mut waiter = target.open(Some(&token)).await;
    settle().await;
    let mut pairer = target.open(Some(&token)).await;

    pairer.send(b"hello-from-pairer", 0).await;
    waiter.expect_frames(1, FINISH).await;
    waiter.send(b"hello-from-waiter", 0).await;
    pairer.expect_frames(1, FINISH).await;

    // Clean close: both sides half-close; both RPCs should end OK.
    waiter.half_close();
    pairer.half_close();

    let waiter = waiter.finish(FINISH).await;
    let pairer = pairer.finish(FINISH).await;
    ScenarioObservation {
        scenario: name.to_string(),
        peers: BTreeMap::from([("waiter".into(), waiter), ("pairer".into(), pairer)]),
    }
}

#[derive(Clone, Copy)]
enum Dead {
    Waiter,
    Pairer,
}
#[derive(Clone, Copy)]
enum Kill {
    /// The dying peer cancels its RPC (h2 RST_STREAM CANCEL).
    Rst,
    /// The dying peer's TCP transport is severed (process death).
    Sever,
}
#[derive(Clone, Copy)]
enum Nudge {
    /// The survivor stays idle: its RPC must remain open (the router's
    /// forward joins both pipes; the survivor-side pipe is still blocked in
    /// Recv) — `still_open` within the observation window.
    No,
    /// After the kill (and a settle so the router has already recorded the
    /// dead peer's Recv error as the chronologically-first error), the
    /// survivor sends one frame. Its relay to the dead peer fails, the
    /// survivor's pipe ends, forward returns, and the survivor observes the
    /// terminal event: the first error's status (survivor = pairing peer) or
    /// a clean OK end (survivor = waiter — its handler returns nil).
    Yes,
}

/// (b) One peer dies hard mid-forward — what does the survivor observe?
/// THE key unpinned behavior: the Python exporter retry classifier reads the
/// surviving peer's status message text.
async fn scenario_cancel_mid_forward(
    target: &Target,
    name: &str,
    sub: &str,
    dead: Dead,
    kill: Kill,
    nudge: Nudge,
) -> ScenarioObservation {
    let token = mint(sub);

    // Build waiter/pairer, routing the doomed peer through the severable
    // relay when the kill mode needs transport death.
    let (mut waiter, mut pairer, sever) = match (dead, kill) {
        (Dead::Waiter, Kill::Sever) => {
            let (waiter, kill_tx) = target.open_severable(&token).await;
            settle().await;
            let pairer = target.open(Some(&token)).await;
            (waiter, pairer, Some(kill_tx))
        }
        (Dead::Pairer, Kill::Sever) => {
            let waiter = target.open(Some(&token)).await;
            settle().await;
            let (pairer, kill_tx) = target.open_severable(&token).await;
            (waiter, pairer, Some(kill_tx))
        }
        _ => {
            let waiter = target.open(Some(&token)).await;
            settle().await;
            let pairer = target.open(Some(&token)).await;
            (waiter, pairer, None)
        }
    };

    // Prove the pair is live in both directions before killing.
    pairer.send(b"probe", 0).await;
    waiter.expect_frames(1, FINISH).await;
    waiter.send(b"probe-back", 0).await;
    pairer.expect_frames(1, FINISH).await;

    let survivor_obs = match dead {
        Dead::Waiter => {
            match kill {
                Kill::Rst => waiter.hard_cancel(),
                Kill::Sever => {
                    let _ = sever.expect("sever channel").send(());
                    drop(waiter); // local half; the wire already died
                }
            }
            if let Nudge::Yes = nudge {
                // Let the router observe the death first (deterministic
                // first-error ordering), then poke the dead direction.
                settle().await;
                pairer.send(b"nudge-into-the-void", 0).await;
            }
            pairer.finish(FINISH).await
        }
        Dead::Pairer => {
            match kill {
                Kill::Rst => pairer.hard_cancel(),
                Kill::Sever => {
                    let _ = sever.expect("sever channel").send(());
                    drop(pairer);
                }
            }
            if let Nudge::Yes = nudge {
                settle().await;
                waiter.send(b"nudge-into-the-void", 0).await;
            }
            waiter.finish(FINISH).await
        }
    };

    ScenarioObservation {
        scenario: name.to_string(),
        peers: BTreeMap::from([("survivor".into(), survivor_obs)]),
    }
}

/// (c) A client cancels pre-pairing, then reconnects with the same token and
/// pairs successfully (the stale entry must not poison the rendezvous).
async fn scenario_pre_pairing_cancel_reconnect(target: &Target) -> ScenarioObservation {
    let token = mint("sub-c");

    let first = target.open(Some(&token)).await;
    settle().await;
    first.hard_cancel();
    // Give the router time to run its disconnect cleanup.
    tokio::time::sleep(Duration::from_millis(750)).await;

    let mut waiter = target.open(Some(&token)).await;
    settle().await;
    let mut pairer = target.open(Some(&token)).await;

    pairer.send(b"post-reconnect", 0).await;
    waiter.expect_frames(1, FINISH).await;
    waiter.send(b"post-reconnect-back", 0).await;
    pairer.expect_frames(1, FINISH).await;

    waiter.half_close();
    pairer.half_close();
    let waiter = waiter.finish(FINISH).await;
    let pairer = pairer.finish(FINISH).await;
    ScenarioObservation {
        scenario: "c_pre_pairing_cancel_reconnect".into(),
        peers: BTreeMap::from([("waiter".into(), waiter), ("pairer".into(), pairer)]),
    }
}

/// (d) GOAWAY half-close: waiter sends GOAWAY then half-closes; the reverse
/// direction keeps flowing losslessly; then the pairer closes and both RPCs
/// end cleanly.
async fn scenario_goaway_half_close(target: &Target) -> ScenarioObservation {
    let token = mint("sub-d");
    let mut waiter = target.open(Some(&token)).await;
    settle().await;
    let mut pairer = target.open(Some(&token)).await;

    pairer.send(b"pre-goaway", 0).await;
    waiter.expect_frames(1, FINISH).await;

    // Waiter half-closes: GOAWAY frame then transport half-close.
    waiter.send(b"", 7).await;
    waiter.half_close();
    pairer.expect_frames(1, FINISH).await; // the GOAWAY, verbatim

    // Reverse flow continues after the half-close.
    for i in 0..3 {
        pairer.send(format!("rev-{i}").as_bytes(), 0).await;
    }
    waiter.expect_frames(3, FINISH).await;
    pairer.half_close();

    let waiter = waiter.finish(FINISH).await;
    let pairer = pairer.finish(FINISH).await;
    ScenarioObservation {
        scenario: "d_goaway_half_close".into(),
        peers: BTreeMap::from([("waiter".into(), waiter), ("pairer".into(), pairer)]),
    }
}

/// (e) Admission failures: invalid/expired/wrong-aud/wrong-key tokens and a
/// missing authorization header.
async fn scenario_bad_tokens(target: &Target) -> Vec<ScenarioObservation> {
    let now = unix_now();
    let key = ROUTER_KEY.as_bytes();
    let cases: Vec<(&str, Option<String>)> = vec![
        ("e1_garbage_token", Some("not-a-jwt".to_string())),
        (
            "e2_expired_token",
            Some(mint_claims(
                key,
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "sub-e2", "aud": ["https://jumpstarter.dev/router"],
                    "exp": now - 60, "nbf": now - 120, "iat": now - 120,
                }),
            )),
        ),
        (
            "e3_wrong_audience",
            Some(mint_claims(
                key,
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "sub-e3", "aud": ["https://example.com/not-router"],
                    "exp": now + 600, "iat": now,
                }),
            )),
        ),
        (
            "e4_wrong_key",
            Some(mint_claims(
                b"not-the-router-key",
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "sub-e4", "aud": ["https://jumpstarter.dev/router"],
                    "exp": now + 600, "iat": now,
                }),
            )),
        ),
        ("e5_missing_authorization", None),
    ];

    let mut out = Vec::new();
    for (name, token) in cases {
        let peer = target.open(token.as_deref()).await;
        let obs = peer.finish(FINISH).await;
        out.push(ScenarioObservation {
            scenario: name.to_string(),
            peers: BTreeMap::from([("caller".into(), obs)]),
        });
    }
    out
}

/// (f) Second pairing with the same sub after a completed pair: the router
/// keeps no jti/used-token state, so a 3rd connection waits and a 4th pairs.
async fn scenario_token_reuse_after_pair(target: &Target) -> ScenarioObservation {
    let first = scenario_echo_clean_close(target, "f_reuse_first_pair", "sub-f").await;
    let mut second = scenario_echo_clean_close(target, "f_reuse_second_pair", "sub-f").await;
    // Merge both pairs into one scenario record.
    let mut peers = BTreeMap::new();
    for (role, obs) in first.peers {
        peers.insert(format!("first_{role}"), obs);
    }
    for (role, obs) in std::mem::take(&mut second.peers) {
        peers.insert(format!("second_{role}"), obs);
    }
    ScenarioObservation {
        scenario: "f_second_pairing_same_sub".into(),
        peers,
    }
}

/// (h) Zero-length DATA and unknown/reserved frame types round-trip verbatim.
async fn scenario_frames_verbatim(target: &Target) -> ScenarioObservation {
    let token = mint("sub-h");
    let mut waiter = target.open(Some(&token)).await;
    settle().await;
    let mut pairer = target.open(Some(&token)).await;

    let frames: &[(&[u8], i32)] = &[
        (b"", 0),             // zero-length DATA
        (b"rst", 3),          // RST_STREAM with payload
        (b"", 6),             // PING
        (b"mystery", 42),     // unknown enum value
        (b"tail", 7),         // GOAWAY with payload
        (b"after-goaway", 0), // the router does not interpret GOAWAY
    ];
    for (payload, frame_type) in frames {
        pairer.send(payload, *frame_type).await;
    }
    waiter.expect_frames(frames.len(), FINISH).await;

    waiter.half_close();
    pairer.half_close();
    let waiter = waiter.finish(FINISH).await;
    let pairer = pairer.finish(FINISH).await;
    ScenarioObservation {
        scenario: "h_frames_verbatim".into(),
        peers: BTreeMap::from([("waiter".into(), waiter), ("pairer".into(), pairer)]),
    }
}

/// (g) A waiting peer left alone: does the router ever time it out?
/// Spec 06 §3.2: the router has no timers — expected `still_open` after the
/// observation window (default 65 s).
async fn scenario_lone_waiter(target: &Target) -> ScenarioObservation {
    let token = mint("sub-g");
    let waiter = target.open(Some(&token)).await;
    let obs = waiter.finish(lone_waiter_window()).await;
    ScenarioObservation {
        scenario: "g_lone_waiter_no_timeout".into(),
        peers: BTreeMap::from([("waiter".into(), obs)]),
    }
}

// ---------------------------------------------------------------------------
// Diffing
// ---------------------------------------------------------------------------

fn diff_observations(
    left_name: &str,
    left: &[ScenarioObservation],
    right_name: &str,
    right: &[ScenarioObservation],
) -> Vec<String> {
    let mut divergences = Vec::new();
    let left_map: BTreeMap<&str, &ScenarioObservation> =
        left.iter().map(|s| (s.scenario.as_str(), s)).collect();
    let right_map: BTreeMap<&str, &ScenarioObservation> =
        right.iter().map(|s| (s.scenario.as_str(), s)).collect();

    for (name, l) in &left_map {
        match right_map.get(name) {
            None => divergences.push(format!("scenario {name}: missing from {right_name}")),
            Some(r) if l != r => {
                divergences.push(format!(
                    "scenario {name} diverges:\n--- {left_name} ---\n{}\n--- {right_name} ---\n{}",
                    serde_json::to_string_pretty(l).unwrap(),
                    serde_json::to_string_pretty(r).unwrap(),
                ));
            }
            Some(_) => {}
        }
    }
    for name in right_map.keys() {
        if !left_map.contains_key(name) {
            divergences.push(format!("scenario {name}: missing from {left_name}"));
        }
    }
    divergences
}

// ---------------------------------------------------------------------------
// TLS material + fake apiserver + binary spawning
// ---------------------------------------------------------------------------

struct TlsFiles {
    ca_pem: String,
    cert_path: PathBuf,
    key_path: PathBuf,
    /// Identity for the in-process server.
    leaf_cert_pem: String,
    leaf_key_pem: String,
}

fn make_tls(dir: &Path) -> TlsFiles {
    let mut ca_params = rcgen::CertificateParams::default();
    ca_params.is_ca = rcgen::IsCa::Ca(rcgen::BasicConstraints::Unconstrained);
    let ca_key = rcgen::KeyPair::generate().expect("ca keypair");
    let ca_cert = ca_params.self_signed(&ca_key).expect("ca cert");

    let mut leaf_params = rcgen::CertificateParams::default();
    leaf_params.subject_alt_names = vec![
        rcgen::SanType::DnsName(rcgen::Ia5String::try_from("localhost").expect("ia5")),
        rcgen::SanType::IpAddress("127.0.0.1".parse().unwrap()),
        rcgen::SanType::IpAddress("::1".parse().unwrap()),
    ];
    let leaf_key = rcgen::KeyPair::generate().expect("leaf keypair");
    let leaf_cert = leaf_params
        .signed_by(&leaf_key, &ca_cert, &ca_key)
        .expect("leaf cert");

    let cert_path = dir.join("tls.crt");
    let key_path = dir.join("tls.key");
    std::fs::write(&cert_path, leaf_cert.pem()).expect("write cert");
    std::fs::write(&key_path, leaf_key.serialize_pem()).expect("write key");
    TlsFiles {
        ca_pem: ca_cert.pem(),
        cert_path,
        key_path,
        leaf_cert_pem: leaf_cert.pem(),
        leaf_key_pem: leaf_key.serialize_pem(),
    }
}

/// Minimal fake Kubernetes apiserver: legacy JSON discovery plus the one
/// ConfigMap both router binaries fatally require. Returns its port.
async fn start_fake_apiserver() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind fakeapi");
    let port = listener.local_addr().unwrap().port();

    const CONFIG_YAML: &str =
        "grpc:\n  keepalive:\n    minTime: 1s\n    permitWithoutStream: true\n";
    let routes: BTreeMap<&'static str, serde_json::Value> = BTreeMap::from([
        (
            "/api",
            serde_json::json!({"kind": "APIVersions", "versions": ["v1"],
                "serverAddressByClientCIDRs": [{"clientCIDR": "0.0.0.0/0", "serverAddress": format!("127.0.0.1:{port}")}]}),
        ),
        (
            "/apis",
            serde_json::json!({"kind": "APIGroupList", "apiVersion": "v1", "groups": []}),
        ),
        (
            "/api/v1",
            serde_json::json!({"kind": "APIResourceList", "groupVersion": "v1", "resources": [
                {"name": "configmaps", "singularName": "configmap", "namespaced": true,
                 "kind": "ConfigMap", "verbs": ["get", "list", "watch"]}]}),
        ),
        (
            "/api/v1/namespaces/default/configmaps/jumpstarter-controller",
            serde_json::json!({"kind": "ConfigMap", "apiVersion": "v1",
                "metadata": {"name": "jumpstarter-controller", "namespace": "default",
                             "uid": "11111111-2222-3333-4444-555555555555", "resourceVersion": "1"},
                "data": {"config": CONFIG_YAML}}),
        ),
    ]);

    tokio::spawn(async move {
        loop {
            let Ok((mut socket, _)) = listener.accept().await else {
                return;
            };
            let routes = routes.clone();
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 4096];
                // Read until end of headers (requests are body-less GETs).
                while !buf.windows(4).any(|w| w == b"\r\n\r\n") {
                    match socket.read(&mut chunk).await {
                        Ok(0) | Err(_) => return,
                        Ok(n) => buf.extend_from_slice(&chunk[..n]),
                    }
                }
                let request_line = String::from_utf8_lossy(&buf);
                let path = request_line
                    .split_whitespace()
                    .nth(1)
                    .unwrap_or("/")
                    .split('?')
                    .next()
                    .unwrap_or("/")
                    .to_string();
                let (status, body) = match routes.get(path.as_str()) {
                    Some(body) => ("200 OK", body.to_string()),
                    None => (
                        "404 Not Found",
                        serde_json::json!({"kind": "Status", "apiVersion": "v1",
                            "status": "Failure", "reason": "NotFound",
                            "message": format!("not found: {path}"), "code": 404})
                        .to_string(),
                    ),
                };
                let response = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(response.as_bytes()).await;
                let _ = socket.shutdown().await;
            });
        }
    });
    port
}

fn write_kubeconfig(dir: &Path, apiserver_port: u16) -> PathBuf {
    let path = dir.join("kubeconfig");
    std::fs::write(
        &path,
        format!(
            r#"apiVersion: v1
kind: Config
clusters:
- cluster:
    server: http://127.0.0.1:{apiserver_port}
  name: fake
contexts:
- context:
    cluster: fake
    namespace: default
    user: fake
  name: fake
current-context: fake
users:
- name: fake
  user: {{}}
"#
        ),
    )
    .expect("write kubeconfig");
    path
}

/// A spawned router binary; killed on drop.
struct RouterProcess {
    child: std::process::Child,
    log_path: PathBuf,
}

impl Drop for RouterProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Where the spawned routers will actually listen. Both binaries hard-code
/// `:8083` with no override; when 8083 is free (the CI case) they are run
/// as-is. When it is occupied (e.g. a local kind cluster publishing the
/// router port), the harness compiles a tiny `bind()`-interpose shim and
/// injects it into the spawned router processes only
/// (`DYLD_INSERT_LIBRARIES` on macOS / `LD_PRELOAD` elsewhere), rewriting
/// binds of port 8083 to a free port. Caveat: on Linux, Go performs raw
/// syscalls so `LD_PRELOAD` cannot intercept the Go router's bind — the shim
/// path is only load-bearing on macOS dev machines (Go uses libSystem there).
struct PortPlan {
    port: u16,
    shim: Option<PathBuf>,
}

async fn port_8083_free() -> bool {
    let v6 = TcpListener::bind("[::]:8083").await;
    let v4 = TcpListener::bind("0.0.0.0:8083").await;
    matches!((&v6, &v4), (Ok(_), Ok(_)))
}

const SHIM_SOURCE: &str = r#"
#define _GNU_SOURCE
#include <sys/socket.h>
#include <netinet/in.h>
#include <stdlib.h>
#include <string.h>

static int rewritten_bind(int fd, const struct sockaddr *addr, socklen_t len);

#ifdef __APPLE__
static int shim_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    return rewritten_bind(fd, addr, len);
}
__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)shim_bind, (const void *)bind},
};
#define REAL_BIND bind
#else
#include <dlfcn.h>
static int (*real_bind)(int, const struct sockaddr *, socklen_t);
int bind(int fd, const struct sockaddr *addr, socklen_t len) {
    if (!real_bind) real_bind = dlsym(RTLD_NEXT, "bind");
    return rewritten_bind(fd, addr, len);
}
#define REAL_BIND real_bind
#endif

static int rewritten_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    struct sockaddr_storage copy;
    const char *from = getenv("JMP_BIND_PORT_FROM");
    const char *to = getenv("JMP_BIND_PORT_TO");
    if (addr && from && to && len <= sizeof(copy)) {
        memcpy(&copy, addr, len);
        in_port_t fromp = htons((in_port_t)atoi(from));
        in_port_t top = htons((in_port_t)atoi(to));
        if (copy.ss_family == AF_INET &&
            ((struct sockaddr_in *)&copy)->sin_port == fromp) {
            ((struct sockaddr_in *)&copy)->sin_port = top;
            return REAL_BIND(fd, (struct sockaddr *)&copy, len);
        }
        if (copy.ss_family == AF_INET6 &&
            ((struct sockaddr_in6 *)&copy)->sin6_port == fromp) {
            ((struct sockaddr_in6 *)&copy)->sin6_port = top;
            return REAL_BIND(fd, (struct sockaddr *)&copy, len);
        }
    }
    return REAL_BIND(fd, addr, len);
}
"#;

fn build_bind_shim(dir: &Path) -> PathBuf {
    let src = dir.join("bind_shim.c");
    std::fs::write(&src, SHIM_SOURCE).expect("write shim source");
    let out = dir.join(if cfg!(target_os = "macos") {
        "bind_shim.dylib"
    } else {
        "bind_shim.so"
    });
    let mut cmd = std::process::Command::new("cc");
    if cfg!(target_os = "macos") {
        cmd.args(["-O2", "-dynamiclib"]);
    } else {
        cmd.args(["-O2", "-shared", "-fPIC"]);
    }
    cmd.arg("-o").arg(&out).arg(&src);
    if !cfg!(target_os = "macos") {
        cmd.arg("-ldl");
    }
    let status = cmd.status().expect("run cc for bind shim");
    assert!(status.success(), "bind shim compilation failed");
    out
}

async fn plan_port(dir: &Path) -> PortPlan {
    if port_8083_free().await {
        return PortPlan {
            port: ROUTER_PORT,
            shim: None,
        };
    }
    let free = TcpListener::bind("127.0.0.1:0").await.expect("probe port");
    let port = free.local_addr().unwrap().port();
    drop(free);
    eprintln!(
        "port {ROUTER_PORT} is occupied; injecting a bind() shim into the spawned \
         routers to relocate them to :{port}"
    );
    PortPlan {
        port,
        shim: Some(build_bind_shim(dir)),
    }
}

async fn spawn_router(
    label: &str,
    bin: &str,
    dir: &Path,
    tls: &TlsFiles,
    kubeconfig: &Path,
    plan: &PortPlan,
    target: &Target,
) -> RouterProcess {
    let log_path = dir.join(format!("{label}.log"));
    let log = std::fs::File::create(&log_path).expect("create log");
    let mut cmd = std::process::Command::new(bin);
    cmd.env("KUBECONFIG", kubeconfig)
        .env("NAMESPACE", "default")
        .env("ROUTER_KEY", ROUTER_KEY)
        .env("EXTERNAL_CERT_PEM", &tls.cert_path)
        .env("EXTERNAL_KEY_PEM", &tls.key_path)
        .env("GRPC_ROUTER_ENDPOINT", "localhost:8083")
        .stdout(log.try_clone().expect("clone log"))
        .stderr(log);
    if let Some(shim) = &plan.shim {
        let inject = if cfg!(target_os = "macos") {
            "DYLD_INSERT_LIBRARIES"
        } else {
            "LD_PRELOAD"
        };
        cmd.env(inject, shim)
            .env("JMP_BIND_PORT_FROM", ROUTER_PORT.to_string())
            .env("JMP_BIND_PORT_TO", plan.port.to_string());
    }
    let child = cmd
        .spawn()
        .unwrap_or_else(|err| panic!("spawn {label} router {bin}: {err}"));
    let mut process = RouterProcess { child, log_path };

    // Ready when a TLS gRPC connect succeeds.
    let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
    loop {
        if let Some(status) = process.child.try_wait().expect("try_wait") {
            let log = std::fs::read_to_string(&process.log_path).unwrap_or_default();
            panic!("{label} router exited during startup ({status}); log:\n{log}");
        }
        let connect = Channel::from_shared(format!("https://localhost:{}", plan.port))
            .expect("uri")
            .tls_config(
                ClientTlsConfig::new()
                    .ca_certificate(Certificate::from_pem(target.ca_pem.clone()))
                    .domain_name("localhost"),
            )
            .expect("client tls")
            .connect()
            .await;
        if connect.is_ok() {
            return process;
        }
        if tokio::time::Instant::now() >= deadline {
            let log = std::fs::read_to_string(&process.log_path).unwrap_or_default();
            panic!(
                "{label} router never became ready on :{}; log:\n{log}",
                plan.port
            );
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
}

async fn wait_port_released(port: u16) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
    loop {
        let probe = TcpListener::bind(("127.0.0.1", port)).await;
        if let Ok(listener) = probe {
            drop(listener);
            return;
        }
        if tokio::time::Instant::now() >= deadline {
            panic!("port {port} was not released after killing the router");
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

fn temp_dir() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("jmp-router-differential-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Env-gated Go-vs-Rust differential run. Records the Go observations as the
/// committed golden and fails on any divergence between the two binaries.
#[tokio::test(flavor = "multi_thread")]
async fn differential_go_vs_rust() {
    let (Ok(go_bin), Ok(rust_bin)) = (
        std::env::var("JMP_GO_ROUTER_BIN"),
        std::env::var("JMP_RUST_ROUTER_BIN"),
    ) else {
        eprintln!(
            "skipping differential_go_vs_rust: set JMP_GO_ROUTER_BIN and JMP_RUST_ROUTER_BIN"
        );
        return;
    };

    let dir = temp_dir();
    let tls = make_tls(&dir);
    let apiserver_port = start_fake_apiserver().await;
    let kubeconfig = write_kubeconfig(&dir, apiserver_port);
    let plan = plan_port(&dir).await;
    let target = Target {
        addr: format!("127.0.0.1:{}", plan.port).parse().unwrap(),
        ca_pem: tls.ca_pem.clone(),
    };

    // Go run: record goldens.
    let go_process = spawn_router("go", &go_bin, &dir, &tls, &kubeconfig, &plan, &target).await;
    let go_obs = run_scenarios(&target).await;
    drop(go_process);
    wait_port_released(plan.port).await;

    let golden = GoldenFile {
        description: format!(
            "Observed behavior of the Go router (controller/cmd/router) recorded by \
             rust/jumpstarter-router-service/tests/differential.rs; regenerate by running \
             that test with JMP_GO_ROUTER_BIN and JMP_RUST_ROUTER_BIN set. Binary: {go_bin}"
        ),
        scenarios: go_obs.clone(),
    };
    std::fs::create_dir_all(Path::new(GOLDEN_PATH).parent().unwrap()).expect("golden dir");
    std::fs::write(
        GOLDEN_PATH,
        serde_json::to_string_pretty(&golden).unwrap() + "\n",
    )
    .expect("write golden");
    eprintln!("recorded Go goldens to {GOLDEN_PATH}");

    // Rust run: diff.
    let rust_process =
        spawn_router("rust", &rust_bin, &dir, &tls, &kubeconfig, &plan, &target).await;
    let rust_obs = run_scenarios(&target).await;
    drop(rust_process);
    wait_port_released(plan.port).await;

    let divergences = diff_observations("go", &go_obs, "rust", &rust_obs);
    assert!(
        divergences.is_empty(),
        "Go and Rust routers diverge in {} scenario(s):\n\n{}",
        divergences.len(),
        divergences.join("\n\n")
    );
}

/// Non-gated replay: the in-process Rust router (same tonic/TLS stack the
/// `jumpstarter-router` binary assembles) must match the committed Go
/// goldens, so CI without a Go toolchain still enforces parity.
#[tokio::test(flavor = "multi_thread")]
async fn rust_router_matches_recorded_go_goldens() {
    use jumpstarter_router_service::compression::MirrorGzipLayer;
    use jumpstarter_router_service::RouterService;
    use tokio_stream::wrappers::TcpListenerStream;
    use tonic::transport::{Identity, Server, ServerTlsConfig};

    // Pin the rustls backend, as the production binaries do (`controller-manager/src/main.rs`):
    // without this the test panics whenever the `cargo test -p …` invocation's feature
    // unification enables zero-or-two default crypto providers.
    let _ = rustls::crypto::ring::default_provider().install_default();

    let golden_raw = std::fs::read_to_string(GOLDEN_PATH).unwrap_or_else(|err| {
        panic!(
            "missing Go goldens at {GOLDEN_PATH} ({err}); record them by running \
             differential_go_vs_rust with JMP_GO_ROUTER_BIN and JMP_RUST_ROUTER_BIN set"
        )
    });
    let golden: GoldenFile = serde_json::from_str(&golden_raw).expect("parse golden file");

    let dir = temp_dir();
    let tls = make_tls(&dir);
    let service = RouterService::with_static_key(ROUTER_KEY.as_bytes().to_vec());
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local addr");
    tokio::spawn(
        Server::builder()
            .tls_config(ServerTlsConfig::new().identity(Identity::from_pem(
                tls.leaf_cert_pem.clone(),
                tls.leaf_key_pem.clone(),
            )))
            .expect("server tls")
            // Gzip parity stack, exactly as the binary assembles it
            // (cmd/router/main.go:34 blank import; mirror-only responses).
            .layer(MirrorGzipLayer)
            .add_service(service.into_server())
            .serve_with_incoming(TcpListenerStream::new(listener)),
    );

    let target = Target {
        addr,
        ca_pem: tls.ca_pem.clone(),
    };
    let rust_obs = run_scenarios(&target).await;

    let divergences = diff_observations("golden(go)", &golden.scenarios, "rust", &rust_obs);
    assert!(
        divergences.is_empty(),
        "Rust router diverges from the recorded Go goldens in {} scenario(s):\n\n{}",
        divergences.len(),
        divergences.join("\n\n")
    );
}
