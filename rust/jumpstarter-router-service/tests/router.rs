//! End-to-end tests of the router rendezvous/forwarding over a real tonic
//! client/server on localhost, exercising the behavioral contract ported
//! from the Go router (`controller/internal/service/router_service.go` +
//! `router_support.go`; specs/rust-core/06-streams-and-router.md §3).

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use jsonwebtoken::{Algorithm, EncodingKey, Header};
use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_router_service::compression::MirrorGzipLayer;
use jumpstarter_router_service::RouterService;
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_stream::wrappers::{ReceiverStream, TcpListenerStream};
use tonic::codec::CompressionEncoding;
use tonic::transport::{Channel, Server};
use tonic::{Code, Request, Status, Streaming};

const KEY: &[u8] = b"integration-test-router-key";

/// A running router with introspection access.
struct Harness {
    endpoint: String,
    addr: std::net::SocketAddr,
    service: RouterService,
}

async fn start_router() -> Harness {
    let service = RouterService::with_static_key(KEY.to_vec());
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local addr");
    // Same compression stack as the jumpstarter-router binary: gzip enabled
    // on the service, response compression mirror-only (the Go gzip codec
    // registration, cmd/router/main.go:34).
    tokio::spawn(
        Server::builder()
            .layer(MirrorGzipLayer)
            .add_service(service.clone().into_server())
            .serve_with_incoming(TcpListenerStream::new(listener)),
    );
    Harness {
        endpoint: format!("http://{addr}"),
        addr,
        service,
    }
}

/// A single-connection TCP proxy whose transport can be severed on command —
/// the deterministic stand-in for a peer crashing / cancelling mid-stream
/// (the h2 connection dies without any clean stream close).
async fn severable_proxy(
    target: std::net::SocketAddr,
) -> (String, tokio::sync::oneshot::Sender<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind proxy");
    let addr = listener.local_addr().expect("proxy addr");
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
    (format!("http://{addr}"), kill_tx)
}

fn unix_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
}

/// Mints a Dial-shaped router token (spec 02 §6.2) with the given TTL.
fn mint_ttl(key: &[u8], sub: &str, ttl_secs: i64) -> String {
    let now = unix_now();
    mint_claims(
        key,
        serde_json::json!({
            "iss": "https://jumpstarter.dev/stream",
            "sub": sub,
            "aud": ["https://jumpstarter.dev/router"],
            "exp": now + ttl_secs,
            "nbf": now,
            "iat": now,
            "jti": uuid::Uuid::new_v4().to_string(),
        }),
    )
}

fn mint(key: &[u8], sub: &str) -> String {
    mint_ttl(key, sub, 1800)
}

fn mint_claims(key: &[u8], claims: serde_json::Value) -> String {
    jsonwebtoken::encode(
        &Header::new(Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(key),
    )
    .expect("encode token")
}

/// One connected peer. Dropping the whole struct aborts the RPC; dropping
/// only `tx` half-closes the send direction (client `done_writing`).
struct Peer {
    tx: mpsc::Sender<pb::StreamRequest>,
    inbound: Streaming<pb::StreamResponse>,
    _channel: Channel,
}

impl std::fmt::Debug for Peer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Peer")
    }
}

impl Peer {
    async fn send(&self, payload: &[u8], frame_type: i32) {
        self.tx
            .send(pb::StreamRequest {
                payload: payload.to_vec(),
                frame_type,
            })
            .await
            .expect("send frame");
    }

    async fn recv(&mut self) -> Result<Option<pb::StreamResponse>, Status> {
        tokio::time::timeout(Duration::from_secs(10), self.inbound.message())
            .await
            .expect("recv timed out")
    }

    /// Expects a DATA-equivalent frame and returns it.
    async fn expect_frame(&mut self) -> pb::StreamResponse {
        self.recv()
            .await
            .expect("stream errored")
            .expect("stream ended early")
    }
}

/// Opens a `Stream` RPC. `Ok` means the server handler accepted the stream
/// (the peer is now parked or paired — tonic returns response headers when
/// the handler returns).
async fn open(endpoint: &str, token: &str) -> Result<Peer, Status> {
    let channel = Channel::from_shared(endpoint.to_string())
        .expect("uri")
        .connect()
        .await
        .expect("connect");
    let mut client = RouterServiceClient::new(channel.clone());
    let (tx, rx) = mpsc::channel::<pb::StreamRequest>(64);
    let mut request = Request::new(ReceiverStream::new(rx));
    request.metadata_mut().insert(
        "authorization",
        format!("Bearer {token}").parse().expect("metadata value"),
    );
    let response = client.stream(request).await?;
    Ok(Peer {
        tx,
        inbound: response.into_inner(),
        _channel: channel,
    })
}

/// Opens a `Stream` RPC with a compression-configured client and also
/// reports the server's response `grpc-encoding` header (present with value
/// `gzip` iff the server compresses this RPC's responses).
async fn open_with_compression(
    endpoint: &str,
    token: &str,
    send_gzip: bool,
    accept_gzip: bool,
) -> (Peer, Option<String>) {
    let channel = Channel::from_shared(endpoint.to_string())
        .expect("uri")
        .connect()
        .await
        .expect("connect");
    let mut client = RouterServiceClient::new(channel.clone());
    if send_gzip {
        // Sets `grpc-encoding: gzip` and compresses every request message.
        client = client.send_compressed(CompressionEncoding::Gzip);
    }
    if accept_gzip {
        // Advertises `grpc-accept-encoding: gzip` and transparently
        // decompresses compressed response messages.
        client = client.accept_compressed(CompressionEncoding::Gzip);
    }
    let (tx, rx) = mpsc::channel::<pb::StreamRequest>(64);
    let mut request = Request::new(ReceiverStream::new(rx));
    request.metadata_mut().insert(
        "authorization",
        format!("Bearer {token}").parse().expect("metadata value"),
    );
    let response = client.stream(request).await.expect("open stream");
    let response_encoding = response
        .metadata()
        .get("grpc-encoding")
        .map(|value| value.to_str().expect("ascii grpc-encoding").to_string());
    (
        Peer {
            tx,
            inbound: response.into_inner(),
            _channel: channel,
        },
        response_encoding,
    )
}

/// Polls until the pending entry for `sub` is gone (disconnect guard ran).
async fn wait_not_pending(service: &RouterService, sub: &str) {
    for _ in 0..200 {
        if !service.is_pending(sub) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!("pending entry for {sub} was never cleaned up");
}

#[tokio::test]
async fn pairs_and_forwards_bidirectionally() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-basic");

    let mut a = open(&harness.endpoint, &token).await.expect("open a");
    let mut b = open(&harness.endpoint, &token).await.expect("open b");

    a.send(b"hello from a", 0).await;
    b.send(b"hello from b", 0).await;

    assert_eq!(b.expect_frame().await.payload, b"hello from a");
    assert_eq!(a.expect_frame().await.payload, b"hello from b");

    // Pairing removed the rendezvous entry.
    assert!(!harness.service.is_pending("sub-basic"));
}

/// No jti tracking (spec 02 §6.2): the same still-valid token pairs again —
/// the 3rd connection becomes a fresh waiter, the 4th pairs with it.
#[tokio::test]
async fn token_reuse_within_ttl_third_waits_fourth_pairs() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-reuse");

    let mut c1 = open(&harness.endpoint, &token).await.expect("open c1");
    let mut c2 = open(&harness.endpoint, &token).await.expect("open c2");
    c1.send(b"one", 0).await;
    assert_eq!(c2.expect_frame().await.payload, b"one");
    assert!(!harness.service.is_pending("sub-reuse"));

    // Third connection with the same token: fresh waiter.
    let mut c3 = open(&harness.endpoint, &token).await.expect("open c3");
    assert!(
        harness.service.is_pending("sub-reuse"),
        "3rd conn must wait"
    );

    // Fourth pairs with the third; the first tunnel is unaffected.
    let mut c4 = open(&harness.endpoint, &token).await.expect("open c4");
    assert!(!harness.service.is_pending("sub-reuse"));

    c3.send(b"three", 0).await;
    assert_eq!(c4.expect_frame().await.payload, b"three");
    c4.send(b"four", 0).await;
    assert_eq!(c3.expect_frame().await.payload, b"four");

    // First pair still flows.
    c2.send(b"two", 0).await;
    assert_eq!(c1.expect_frame().await.payload, b"two");
}

/// Two peers racing to be first on one sub: exactly one parks, the other
/// pairs with it (DashMap entry() = Go LoadOrStore atomicity).
#[tokio::test]
async fn two_first_peers_race_pairs_exactly_once() {
    let harness = start_router().await;

    for round in 0..10 {
        let sub = format!("sub-race-{round}");
        let token = mint(KEY, &sub);
        let (a, b) = tokio::join!(
            open(&harness.endpoint, &token),
            open(&harness.endpoint, &token)
        );
        let (mut a, mut b) = (a.expect("open a"), b.expect("open b"));

        // If both had parked (two waiters) the map would still hold an entry
        // and neither would ever receive the other's frames.
        assert!(
            !harness.service.is_pending(&sub),
            "round {round}: exactly one peer must have waited"
        );

        a.send(format!("a{round}").as_bytes(), 0).await;
        b.send(format!("b{round}").as_bytes(), 0).await;
        assert_eq!(
            b.expect_frame().await.payload,
            format!("a{round}").as_bytes()
        );
        assert_eq!(
            a.expect_frame().await.payload,
            format!("b{round}").as_bytes()
        );
    }
}

/// A pre-pairing disconnect removes the waiter's entry, and a later waiter
/// with the same sub remains pairable — the id guard means stale cleanup can
/// never delete the newer entry (unit-level pin of the CompareAndDelete
/// semantics lives in service.rs `remove_stale_is_id_guarded`).
#[tokio::test]
async fn pre_pairing_disconnect_removes_entry_but_reconnect_survives() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-disconnect");

    let c1 = open(&harness.endpoint, &token).await.expect("open c1");
    assert!(harness.service.is_pending("sub-disconnect"));

    // Abort the waiter's RPC (drop sender, inbound and channel).
    drop(c1);
    wait_not_pending(&harness.service, "sub-disconnect").await;

    // A reconnect parks a fresh entry...
    let mut c2 = open(&harness.endpoint, &token).await.expect("open c2");
    assert!(harness.service.is_pending("sub-disconnect"));

    // ...which must still be there after any stale cleanup had time to run,
    // and must be pairable.
    tokio::time::sleep(Duration::from_millis(250)).await;
    assert!(
        harness.service.is_pending("sub-disconnect"),
        "stale cleanup must not remove the reconnect's entry"
    );

    let mut c3 = open(&harness.endpoint, &token).await.expect("open c3");
    c2.send(b"ping", 0).await;
    assert_eq!(c3.expect_frame().await.payload, b"ping");
    c3.send(b"pong", 0).await;
    assert_eq!(c2.expect_frame().await.payload, b"pong");
}

/// GOAWAY half-close ordering: A sends GOAWAY and closes its send side; the
/// B→A direction keeps flowing losslessly until B closes, then both RPCs end
/// cleanly (a clean EOF never cancels the opposite pipe).
#[tokio::test]
async fn goaway_half_close_keeps_reverse_direction_flowing() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-halfclose");

    let a = open(&harness.endpoint, &token).await.expect("open a");
    let mut b = open(&harness.endpoint, &token).await.expect("open b");

    // A: GOAWAY frame (frame_type 7), then transport half-close.
    a.send(b"", 7).await;
    let a_tx = a.tx.clone();
    drop(a_tx);
    let Peer {
        tx,
        inbound,
        _channel,
    } = a;
    drop(tx); // half-close A's send direction
    let mut a_inbound = inbound;

    // B observes the GOAWAY frame verbatim.
    let goaway = tokio::time::timeout(Duration::from_secs(10), b.inbound.message())
        .await
        .expect("recv goaway timed out")
        .expect("b stream errored")
        .expect("b stream ended before goaway");
    assert_eq!(goaway.frame_type, 7);
    assert_eq!(goaway.payload, b"");

    // B → A keeps flowing after A's half-close.
    const N: usize = 100;
    for i in 0..N {
        b.send(format!("frame-{i}").as_bytes(), 0).await;
    }
    // B closes its send side too.
    let Peer {
        tx: b_tx,
        inbound: mut b_inbound,
        _channel: b_channel,
    } = b;
    drop(b_tx);

    // A receives every frame in order, then a clean end of stream.
    for i in 0..N {
        let frame = tokio::time::timeout(Duration::from_secs(10), a_inbound.message())
            .await
            .expect("recv timed out")
            .expect("a stream errored")
            .unwrap_or_else(|| panic!("a stream ended early at frame {i}"));
        assert_eq!(frame.payload, format!("frame-{i}").as_bytes());
    }
    let end = tokio::time::timeout(Duration::from_secs(10), a_inbound.message())
        .await
        .expect("recv timed out")
        .expect("a must end OK, not with an error");
    assert!(end.is_none(), "a must see clean end of stream");

    // B's RPC also ends cleanly (forward finished with no error).
    let end = tokio::time::timeout(Duration::from_secs(10), b_inbound.message())
        .await
        .expect("recv timed out")
        .expect("b must end OK, not with an error");
    assert!(end.is_none(), "b must see clean end of stream");
    drop(b_channel);
}

/// Zero-length DATA frames and unknown frame_type values are forwarded
/// verbatim (Python PING semantics depend on this, spec 06 §2.3).
#[tokio::test]
async fn zero_length_data_and_unknown_frame_types_forward_verbatim() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-frames");

    let a = open(&harness.endpoint, &token).await.expect("open a");
    let mut b = open(&harness.endpoint, &token).await.expect("open b");

    let frames: &[(&[u8], i32)] = &[
        (b"", 0),             // zero-length DATA
        (b"rst", 3),          // RST_STREAM, with payload
        (b"", 6),             // PING
        (b"mystery", 42),     // unknown enum value
        (b"tail", 7),         // GOAWAY with payload
        (b"after-goaway", 0), // the router does not interpret GOAWAY
    ];
    for (payload, frame_type) in frames {
        a.send(payload, *frame_type).await;
    }
    for (payload, frame_type) in frames {
        let frame = b.expect_frame().await;
        assert_eq!(&frame.payload, payload);
        assert_eq!(frame.frame_type, *frame_type);
    }
}

/// gzip round trip — the Go router registers the gRPC gzip codec
/// (`cmd/router/main.go:34`, spec 06 §3.1), so a gzip-compressing peer must
/// be admitted and forwarded (pre-fix the Rust router rejected it with
/// UNIMPLEMENTED), and its responses must be gzip-compressed: with no
/// server-wide compressor configured, grpc-go mirrors the request's encoding
/// (grpc-go v1.80.0 — controller/go.mod's version — server.go:1684-1700).
#[tokio::test]
async fn gzip_compressing_peer_round_trips_with_mirrored_responses() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-gzip");

    // Peer A compresses its requests and — like every real gRPC stack that
    // sends gzip — advertises accepting it. Peer B is a stock identity peer.
    let (mut a, a_encoding) = open_with_compression(&harness.endpoint, &token, true, true).await;
    let mut b = open(&harness.endpoint, &token).await.expect("open b");

    // A → B: the router decompresses at its edge and forwards verbatim.
    let payload = vec![0x5A; 32 * 1024];
    a.send(&payload, 0).await;
    let frame = b.expect_frame().await;
    assert_eq!(frame.payload, payload);
    assert_eq!(frame.frame_type, 0);

    // B → A: A's responses are gzip on the wire (the client codec
    // transparently decompresses here); frame_type rides along untouched.
    b.send(b"reply-to-gzip-peer", 3).await;
    let frame = a.expect_frame().await;
    assert_eq!(frame.payload, b"reply-to-gzip-peer");
    assert_eq!(frame.frame_type, 3);

    // Mirror parity: the gzip-sending peer's responses are gzip-compressed.
    assert_eq!(
        a_encoding.as_deref(),
        Some("gzip"),
        "gzip requests must get gzip responses (grpc-go mirrors the request encoding)"
    );
}

/// grpc-go compresses responses only as a mirror of the request's encoding —
/// merely advertising `grpc-accept-encoding: gzip` while sending identity
/// (what every stock grpc-c/grpcio peer does on every call) must NOT trigger
/// response compression (grpc-go v1.80.0 server.go:1684-1700 consults
/// `RecvCompress`, never the accept header). Locks `MirrorGzipLayer`:
/// without it, tonic's `send_compressed(Gzip)` would gzip every frame to
/// every stock grpcio peer on the forwarding hot path where Go sends
/// identity.
#[tokio::test]
async fn identity_peer_advertising_gzip_gets_identity_responses() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-accept-only");

    let (mut a, a_encoding) = open_with_compression(&harness.endpoint, &token, false, true).await;
    let (mut b, b_encoding) = open_with_compression(&harness.endpoint, &token, false, true).await;

    a.send(b"identity-forward", 0).await;
    assert_eq!(b.expect_frame().await.payload, b"identity-forward");
    b.send(b"identity-reverse", 0).await;
    assert_eq!(a.expect_frame().await.payload, b"identity-reverse");

    for (peer, encoding) in [("waiter", &a_encoding), ("pairer", &b_encoding)] {
        assert_ne!(
            encoding.as_deref(),
            Some("gzip"),
            "{peer}: identity requests must get identity responses \
             (Go mirrors the request encoding, got grpc-encoding: {encoding:?})"
        );
    }
}

/// Token expiry is admission-only: an established stream survives its token
/// expiring; a new connection with the expired token is rejected.
#[tokio::test]
async fn established_stream_survives_token_expiry() {
    let harness = start_router().await;
    let token = mint_ttl(KEY, "sub-expiry", 2);

    let mut a = open(&harness.endpoint, &token).await.expect("open a");
    let mut b = open(&harness.endpoint, &token).await.expect("open b");
    a.send(b"before", 0).await;
    assert_eq!(b.expect_frame().await.payload, b"before");

    // Outlive the token.
    tokio::time::sleep(Duration::from_millis(2600)).await;

    // The paired stream keeps working...
    a.send(b"after-expiry", 0).await;
    assert_eq!(b.expect_frame().await.payload, b"after-expiry");
    b.send(b"reverse", 0).await;
    assert_eq!(a.expect_frame().await.payload, b"reverse");

    // ...but the expired token no longer admits new streams.
    let err = open(&harness.endpoint, &token).await.expect_err("expired");
    assert_eq!(err.code(), Code::InvalidArgument);
    assert_eq!(err.message(), "invalid jwt token");
}

/// Admission failures over the wire: every JWT defect is
/// `INVALID_ARGUMENT "invalid jwt token"`; bearer-extraction failures keep
/// the Go bearer statuses.
#[tokio::test]
async fn invalid_tokens_are_rejected_with_go_statuses() {
    let harness = start_router().await;
    let now = unix_now();

    let jwt_cases: Vec<(&str, String)> = vec![
        ("garbage", "not-a-jwt".to_string()),
        (
            "expired",
            mint_claims(
                KEY,
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "s", "aud": ["https://jumpstarter.dev/router"],
                    "exp": now - 60, "iat": now - 120,
                }),
            ),
        ),
        (
            "bad issuer",
            mint_claims(
                KEY,
                serde_json::json!({
                    "iss": "https://evil.example.com",
                    "sub": "s", "aud": ["https://jumpstarter.dev/router"],
                    "exp": now + 600,
                }),
            ),
        ),
        (
            "bad audience",
            mint_claims(
                KEY,
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "s", "aud": ["https://example.com/not-router"],
                    "exp": now + 600,
                }),
            ),
        ),
        (
            "missing exp",
            mint_claims(
                KEY,
                serde_json::json!({
                    "iss": "https://jumpstarter.dev/stream",
                    "sub": "s", "aud": ["https://jumpstarter.dev/router"],
                }),
            ),
        ),
        ("wrong key", mint(b"wrong-key", "s")),
    ];
    for (name, token) in jwt_cases {
        let err = open(&harness.endpoint, &token).await.unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument, "case {name}");
        assert_eq!(err.message(), "invalid jwt token", "case {name}");
    }

    // A token without iat MUST be accepted (golang-jwt v5 WithIssuedAt).
    let iat_less = mint_claims(
        KEY,
        serde_json::json!({
            "iss": "https://jumpstarter.dev/stream",
            "sub": "sub-no-iat", "aud": ["https://jumpstarter.dev/router"],
            "exp": now + 600,
        }),
    );
    let _peer = open(&harness.endpoint, &iat_less)
        .await
        .expect("iat-less token must be accepted");

    // Missing authorization header: UNAUTHENTICATED (bearer.go:40-42).
    let channel = Channel::from_shared(harness.endpoint.clone())
        .unwrap()
        .connect()
        .await
        .unwrap();
    let mut client = RouterServiceClient::new(channel);
    let (_tx, rx) = mpsc::channel::<pb::StreamRequest>(1);
    let err = client
        .stream(Request::new(ReceiverStream::new(rx)))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::Unauthenticated);
    assert_eq!(err.message(), "missing authorization header");
}

/// Asserts the peer observes NO event (frame, error, or end of stream) for
/// `window` — the survivor-side lock on Go's "forward joins both pipes"
/// shape (goldens b1–b4: `still_open`). The differential replay enforces the
/// full 10 s golden window; this uses a shorter one for test runtime.
async fn assert_still_open(peer: &mut Peer, window: Duration, who: &str) {
    match tokio::time::timeout(window, peer.inbound.message()).await {
        Err(_elapsed) => {}
        Ok(event) => panic!("{who} must stay open (Go still_open), got {event:?}"),
    }
}

/// One peer dying mid-forward does NOT end the survivor's RPC: Go's
/// `Forward` returns `g.Wait()` — it joins BOTH pipe goroutines — and
/// `pipe` never observes the errgroup context (`router_support.go:12-29`,
/// 31-46), so the survivor-side pipe stays blocked in `Recv(survivor)` and
/// the survivor's RPC stays open (measured: goldens b1–b4 record
/// `still_open` survivors over a 10 s window).
///
/// The forward — and with it the survivor's RPC — ends only once the
/// survivor's own pipe ends: here, the survivor sends a frame whose relay
/// to the dead waiter fails, after which the pairing peer's RPC terminates
/// with the chronologically-first error (the dead waiter's Recv error — the
/// Go second handler returns `Forward(...)`'s result).
#[tokio::test]
async fn waiter_death_leaves_pairing_peer_open_until_its_own_pipe_ends() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-err-first");

    // The waiter connects through a severable transport.
    let (proxy_endpoint, kill) = severable_proxy(harness.addr).await;
    let first = open(&proxy_endpoint, &token).await.expect("open first");
    assert!(harness.service.is_pending("sub-err-first"));
    let mut second = open(&harness.endpoint, &token).await.expect("open second");

    // Prove the pair is live.
    first.send(b"x", 0).await;
    assert_eq!(second.expect_frame().await.payload, b"x");

    // Kill the waiter's transport abruptly (no clean half-close).
    kill.send(()).expect("sever waiter transport");

    // Golden b3: the survivor observes nothing — its RPC stays open even
    // though the router has already seen the waiter's transport error.
    assert_still_open(&mut second, Duration::from_secs(2), "pairing peer").await;

    // The survivor sends a frame; relaying it to the dead waiter fails, so
    // the survivor's pipe ends too and forward returns the first error —
    // the dead waiter's Recv error in grpc-go's wire shape. Golden b3n
    // (tests/golden/router_behavior.json): the Go survivor observes
    // CANCELLED "context canceled" (grpc-go's ContextErr(context.Canceled)
    // from the parked Recv), never tonic's native "h2 protocol error: ..."
    // text — the Python exporter retry classifier reads this message.
    second.send(b"probe-into-the-void", 0).await;
    let status = second
        .recv()
        .await
        .expect_err("pairing peer must now see an error status");
    assert_eq!(status.code(), Code::Cancelled, "got {status:?}");
    assert_eq!(status.message(), "context canceled", "got {status:?}");
}

/// The RST-cancel kill shape (golden b1n): the dying waiter's client cancels
/// its RPC and closes its connection, which the router's Recv observes as a
/// CLEAN end — so the only pipe error is the survivor's failed relay into
/// the dead waiter's response stream. grpc-go's `Send` on a stream whose
/// connection went away fails with `ErrConnClosing`, surfacing to the
/// survivor as UNAVAILABLE "transport is closing" (measured golden b1n
/// in tests/golden/router_behavior.json) — not tonic's native send-failure
/// shape.
#[tokio::test]
async fn waiter_cancel_maps_relay_failure_to_transport_is_closing() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-err-cancel");

    let first = open(&harness.endpoint, &token).await.expect("open first");
    assert!(harness.service.is_pending("sub-err-cancel"));
    let mut second = open(&harness.endpoint, &token).await.expect("open second");

    // Prove the pair is live.
    first.send(b"x", 0).await;
    assert_eq!(second.expect_frame().await.payload, b"x");

    // The waiter cancels: dropping the Peer ends its request stream and
    // closes its client connection (the golden-b1n kill shape).
    drop(first);

    // Survivor stays open (Go joins both pipes)...
    assert_still_open(&mut second, Duration::from_secs(2), "pairing peer").await;

    // ...until its own relay to the dead waiter fails.
    second.send(b"probe-into-the-void", 0).await;
    let status = second
        .recv()
        .await
        .expect_err("pairing peer must now see an error status");
    assert_eq!(status.code(), Code::Unavailable, "got {status:?}");
    assert_eq!(status.message(), "transport is closing", "got {status:?}");
}

/// The reverse direction of the same contract: the pairing (second) peer's
/// death leaves the waiting peer's RPC open (golden b4: `still_open`) —
/// Go's waiting handler stays parked until `Forward` returns and the
/// deferred `first.cancel()` runs. Once the waiter's own pipe ends (its
/// relay to the dead pairer fails), the waiter ends `OK`, never with an
/// error (its handler returns `nil`).
#[tokio::test]
async fn pairing_peer_death_leaves_waiter_open_then_ends_ok() {
    let harness = start_router().await;
    let token = mint(KEY, "sub-err-second");

    let mut first = open(&harness.endpoint, &token).await.expect("open first");
    assert!(harness.service.is_pending("sub-err-second"));

    // The pairing peer connects through a severable transport.
    let (proxy_endpoint, kill) = severable_proxy(harness.addr).await;
    let second = open(&proxy_endpoint, &token).await.expect("open second");

    second.send(b"x", 0).await;
    assert_eq!(first.expect_frame().await.payload, b"x");

    kill.send(()).expect("sever pairing peer transport");

    // Golden b4: the waiter observes nothing; its RPC stays open.
    assert_still_open(&mut first, Duration::from_secs(2), "waiter").await;

    // The waiter sends a frame; the relay to the dead pairer fails, both
    // pipes are now done, forward returns, and the waiter's stream must end
    // cleanly (Ok(None)), not with an error.
    first.send(b"probe-into-the-void", 0).await;
    let end = first
        .recv()
        .await
        .expect("waiter must end OK, not with an error");
    assert!(end.is_none(), "waiter must see clean end of stream");
}

/// Bidirectional bulk integrity: many frames both ways concurrently, with
/// order- and content-sensitive checksums on both sides.
#[tokio::test]
async fn bidirectional_large_payload_integrity() {
    const FRAMES: u64 = 100_000;
    const PAYLOAD: usize = 256;

    let harness = start_router().await;
    let token = mint(KEY, "sub-bulk");

    let a = open(&harness.endpoint, &token).await.expect("open a");
    let b = open(&harness.endpoint, &token).await.expect("open b");

    /// Order-sensitive FNV-1a over the byte stream.
    fn fnv1a(hash: u64, bytes: &[u8]) -> u64 {
        bytes.iter().fold(hash, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3)
        })
    }
    const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;

    /// Deterministic payload for frame `i` with a per-direction seed.
    fn payload(seed: u64, i: u64) -> Vec<u8> {
        let mut state = seed ^ i.wrapping_mul(0x9e37_79b9_7f4a_7c15);
        (0..PAYLOAD)
            .map(|_| {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                (state >> 33) as u8
            })
            .collect()
    }

    async fn sender(tx: mpsc::Sender<pb::StreamRequest>, seed: u64) -> u64 {
        let mut hash = FNV_OFFSET;
        for i in 0..FRAMES {
            let payload = payload(seed, i);
            hash = fnv1a(hash, &payload);
            tx.send(pb::StreamRequest {
                payload,
                frame_type: 0,
            })
            .await
            .expect("send bulk frame");
        }
        // tx dropped here: half-close after the last frame.
        hash
    }

    async fn receiver(mut inbound: Streaming<pb::StreamResponse>) -> (u64, u64) {
        let mut hash = FNV_OFFSET;
        let mut count = 0u64;
        while let Some(frame) = inbound.message().await.expect("recv bulk frame") {
            hash = fnv1a(hash, &frame.payload);
            count += 1;
        }
        (hash, count)
    }

    let Peer {
        tx: a_tx,
        inbound: a_in,
        _channel: a_ch,
    } = a;
    let Peer {
        tx: b_tx,
        inbound: b_in,
        _channel: b_ch,
    } = b;

    let a_send = tokio::spawn(sender(a_tx, 0xAAAA));
    let b_send = tokio::spawn(sender(b_tx, 0xBBBB));
    let a_recv = tokio::spawn(receiver(a_in));
    let b_recv = tokio::spawn(receiver(b_in));

    let run = async {
        let (a_sent, b_sent, a_received, b_received) =
            tokio::try_join!(a_send, b_send, a_recv, b_recv).expect("task join");
        (a_sent, b_sent, a_received, b_received)
    };
    let (a_sent, b_sent, (a_hash, a_count), (b_hash, b_count)) =
        tokio::time::timeout(Duration::from_secs(120), run)
            .await
            .expect("bulk transfer timed out");

    assert_eq!(a_count, FRAMES, "a received frame count");
    assert_eq!(b_count, FRAMES, "b received frame count");
    assert_eq!(a_hash, b_sent, "b→a stream integrity");
    assert_eq!(b_hash, a_sent, "a→b stream integrity");

    drop(a_ch);
    drop(b_ch);
}

/// Backpressure shape (Go parity): Go's pipe is a synchronous Recv→Send
/// loop (`router_support.go:12-29`) with ~1 application-held frame per
/// direction, so when one peer stops reading, the other can push only a
/// small, HTTP/2-flow-control-bounded amount of data before the router
/// stops `Recv`ing. This locks the Rust router to that shape: it must not
/// hide a frame-buffer that absorbs a flood on behalf of a stalled reader.
///
/// The pairer floods 1 MiB frames through a depth-1 client channel while
/// the waiter never reads. Completed sends therefore measure exactly what
/// the pipeline absorbed. Budget with `RESPONSE_BUFFER = 2` (all at current
/// hyper/tonic defaults, adaptive windows off): 1 client channel + ~2
/// (client send buffer + router 1 MiB conn window) + 1 pipe in-flight + 2
/// response channel + ~1 encode in-flight + ~2 (waiter client's 2 MiB
/// stream window) ≈ 9 frames. A depth-16 channel alone would absorb ≥ 17
/// (16 buffered + 1 held by the blocked pipe), so the 12-frame ceiling
/// deterministically fails any return to large buffers.
#[tokio::test]
async fn stalled_reader_backpressure_is_go_shaped() {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;

    const PAYLOAD: usize = 1 << 20; // 1 MiB, under the 4 MiB default cap
    const MAX_ABSORBED: u64 = 12;

    let harness = start_router().await;
    let token = mint(KEY, "sub-backpressure");

    // The waiter parks first and never reads its inbound stream.
    let waiter = open(&harness.endpoint, &token).await.expect("open waiter");
    assert!(harness.service.is_pending("sub-backpressure"));

    // The pairer uses a depth-1 request channel so a completed `send`
    // means the frame left the test and entered the wire/router pipeline.
    let channel = Channel::from_shared(harness.endpoint.clone())
        .expect("uri")
        .connect()
        .await
        .expect("connect");
    let mut client = RouterServiceClient::new(channel.clone());
    let (tx, rx) = mpsc::channel::<pb::StreamRequest>(1);
    let mut request = Request::new(ReceiverStream::new(rx));
    request.metadata_mut().insert(
        "authorization",
        format!("Bearer {token}").parse().expect("metadata value"),
    );
    let _pairer_inbound = client
        .stream(request)
        .await
        .expect("open pairer")
        .into_inner();
    assert!(
        !harness.service.is_pending("sub-backpressure"),
        "pairer must have paired with the waiter"
    );

    let absorbed = Arc::new(AtomicU64::new(0));
    let counter = Arc::clone(&absorbed);
    let flood = tokio::spawn(async move {
        loop {
            let frame = pb::StreamRequest {
                payload: vec![0xAB; PAYLOAD],
                frame_type: 0,
            };
            if tx.send(frame).await.is_err() {
                return;
            }
            counter.fetch_add(1, Ordering::Relaxed);
        }
    });

    // Let the pipeline fill against the stalled reader and settle.
    tokio::time::sleep(Duration::from_secs(3)).await;
    let filled = absorbed.load(Ordering::Relaxed);
    assert!(
        filled >= 2,
        "pipeline absorbed only {filled} frames; forwarding never started?"
    );
    assert!(
        filled <= MAX_ABSORBED,
        "router absorbed {filled} x 1 MiB frames for a stalled reader; Go's \
         synchronous Recv→Send pipe bounds this near HTTP/2 flow-control \
         windows (expected <= {MAX_ABSORBED})"
    );

    // And it stays bounded — backpressure holds, it is not merely slow.
    tokio::time::sleep(Duration::from_millis(1500)).await;
    let after = absorbed.load(Ordering::Relaxed);
    assert!(
        after <= MAX_ABSORBED,
        "buffering kept growing against a stalled reader ({after} frames \
         after settling); backpressure is broken"
    );

    flood.abort();
    drop(waiter);
}

/// The same rendezvous over TLS (rcgen CA + localhost leaf, tonic
/// ServerTlsConfig) — the transport the production router serves.
#[tokio::test]
async fn pairs_and_forwards_over_tls() {
    use tonic::transport::{Certificate, ClientTlsConfig, Identity, ServerTlsConfig};

    // Pin the rustls backend (see differential.rs): feature unification across
    // `cargo test -p …` shapes can otherwise leave rustls with no usable default provider.
    let _ = rustls::crypto::ring::default_provider().install_default();

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

    let service = RouterService::with_static_key(KEY.to_vec());
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local addr");
    tokio::spawn(
        Server::builder()
            .tls_config(ServerTlsConfig::new().identity(Identity::from_pem(
                leaf_cert.pem(),
                leaf_key.serialize_pem(),
            )))
            .expect("server tls")
            .layer(MirrorGzipLayer)
            .add_service(service.into_server())
            .serve_with_incoming(TcpListenerStream::new(listener)),
    );

    let token = mint(KEY, "sub-tls");
    let open_tls = |token: String| {
        let ca_pem = ca_cert.pem();
        async move {
            let channel = Channel::from_shared(format!("https://localhost:{}", addr.port()))
                .expect("uri")
                .tls_config(
                    ClientTlsConfig::new()
                        .ca_certificate(Certificate::from_pem(ca_pem))
                        .domain_name("localhost"),
                )
                .expect("client tls")
                .connect()
                .await
                .expect("tls connect");
            let mut client = RouterServiceClient::new(channel.clone());
            let (tx, rx) = mpsc::channel::<pb::StreamRequest>(16);
            let mut request = Request::new(ReceiverStream::new(rx));
            request.metadata_mut().insert(
                "authorization",
                format!("Bearer {token}").parse().expect("metadata value"),
            );
            let inbound = client.stream(request).await.expect("open").into_inner();
            Peer {
                tx,
                inbound,
                _channel: channel,
            }
        }
    };

    let mut a = open_tls(token.clone()).await;
    let mut b = open_tls(token).await;

    a.send(b"tls-hello", 0).await;
    assert_eq!(b.expect_frame().await.payload, b"tls-hello");
    b.send(b"tls-reply", 0).await;
    assert_eq!(a.expect_frame().await.payload, b"tls-reply");
}
