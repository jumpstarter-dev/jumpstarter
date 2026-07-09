//! The `jumpstarter.v1.RouterService` implementation: token-keyed stream
//! rendezvous plus verbatim forwarding, porting `RouterService.Stream`
//! (`controller/internal/service/router_service.go:80-115`).
//!
//! ## Rendezvous (spec 06 §3.2, spec 02 §9.2)
//!
//! One entry per JWT `sub` in a [`DashMap`] — the Go `pending sync.Map`:
//!
//! - the **first** authenticated peer parks its inbound stream + response
//!   sender under its `sub` (Go `LoadOrStore` storing) and its RPC stays
//!   open, producing nothing, until pairing or disconnect (Go: handler
//!   blocks on `<-ctx.Done()`);
//! - a **guard** removes the entry when the first peer's transport dies
//!   before pairing — but only while the entry still belongs to that peer
//!   (Go `CompareAndDelete(streamName, sctx)` compares the stored pointer;
//!   here a unique per-connection `id`), so a reconnect's fresh entry is
//!   never clobbered by stale cleanup;
//! - the **second** peer with the same `sub` removes the entry (Go
//!   `LoadOrStore` loading + `CompareAndDelete`; here the atomic
//!   `Entry::Occupied::remove`) and forwarding starts;
//! - there is **no** token single-use tracking (no `jti` bookkeeping, spec
//!   02 §6.2): after a completed pair, a third connection presenting the
//!   same still-unexpired token becomes a fresh waiter.
//!
//! The router has no timers: token expiry is admission-only, and an
//! established stream survives its token expiring (spec 06 §3.2 timers).
//!
//! ## Status delivery (Go errgroup shape)
//!
//! Forwarding ends only when **both** pipe directions have ended on their
//! own (Go: `Forward` returns `g.Wait()`, and `pipe` never observes the
//! errgroup context) — a peer dying mid-forward therefore leaves the
//! survivor's RPC open, blocked in `Recv`, until the survivor itself ends
//! or sends a frame whose relay fails (goldens b1–b4: `still_open`
//! survivors). Once both pipes end, the chronologically-first error
//! terminates the **pairing** peer's RPC (Go: the second handler returns
//! `Forward(...)`'s result); the waiting peer's RPC always ends `OK` (Go:
//! its handler returns `nil` after its context completes). Clean EOF in one
//! direction never cancels the other (GOAWAY half-close, see
//! [`crate::forward`]).

use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use dashmap::mapref::entry::Entry;
use dashmap::DashMap;
use jumpstarter_protocol::v1 as pb;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::Stream;
use tokio_util::sync::CancellationToken;
use tonic::{Request, Response, Status, Streaming};
use tracing::info;

use crate::auth::{self, KeySource};
use crate::forward::{self, ResponseTx, RESPONSE_BUFFER};

/// The parked half of a waiting first peer: its inbound frame stream and the
/// sender feeding its response stream. (Go `streamContext.stream`.)
struct ParkedPeer {
    inbound: Streaming<pb::StreamRequest>,
    tx: ResponseTx,
}

/// One `pending` map entry (Go `streamContext`).
///
/// `parked` is a `Mutex<Option<..>>` purely for `Sync` (tonic's `Streaming`
/// is `Send` but not `Sync`; the map value must be both) — it is written
/// once at insert and taken once at pairing, never contended.
struct PendingEntry {
    /// Unique per-connection identity standing in for Go's `sctx` pointer in
    /// `CompareAndDelete`: stale cleanup may remove the entry only while it
    /// still belongs to this connection.
    id: u64,
    parked: Mutex<Option<ParkedPeer>>,
    /// Cancelled when the entry is claimed by a pairing peer, stopping the
    /// first peer's disconnect guard (Go's `first.cancel()` unblocking the
    /// parked handler).
    guard_cancel: CancellationToken,
}

type Pending = DashMap<String, PendingEntry>;

/// Go `CompareAndDelete(streamName, sctx)`: remove the entry for `name` only
/// if it is still the one identified by `id`. Returns whether an entry was
/// removed.
fn remove_stale(pending: &Pending, name: &str, id: u64) -> bool {
    pending.remove_if(name, |_, entry| entry.id == id).is_some()
}

/// The router service (Go `RouterService`, `router_service.go:41-45`).
///
/// Cheaply cloneable: clones share the rendezvous map, so a clone can be
/// handed to the tonic server while the original is kept for introspection.
#[derive(Clone)]
pub struct RouterService {
    pending: Arc<Pending>,
    next_id: Arc<AtomicU64>,
    key: KeySource,
}

impl Default for RouterService {
    fn default() -> Self {
        Self::new()
    }
}

impl RouterService {
    /// Production construction: the HMAC key is the raw bytes of env
    /// `ROUTER_KEY`, re-read on every authentication attempt exactly like
    /// the Go keyfunc (`router_service.go:61`).
    pub fn new() -> Self {
        Self::with_key_source(KeySource::Env)
    }

    /// Construction with a fixed key (tests; avoids process-env races).
    pub fn with_static_key(key: Vec<u8>) -> Self {
        Self::with_key_source(KeySource::Static(key))
    }

    /// Construction with an explicit [`KeySource`].
    pub fn with_key_source(key: KeySource) -> Self {
        Self {
            pending: Arc::new(DashMap::new()),
            next_id: Arc::new(AtomicU64::new(1)),
            key,
        }
    }

    /// Number of streams currently waiting for their peer (introspection for
    /// tests/metrics; no Go counterpart).
    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }

    /// Whether a waiter is parked under `name` (introspection for
    /// tests/metrics; no Go counterpart).
    pub fn is_pending(&self, name: &str) -> bool {
        self.pending.contains_key(name)
    }
}

#[tonic::async_trait]
impl pb::router_service_server::RouterService for RouterService {
    type StreamStream =
        Pin<Box<dyn Stream<Item = Result<pb::StreamResponse, Status>> + Send + 'static>>;

    async fn stream(
        &self,
        request: Request<Streaming<pb::StreamRequest>>,
    ) -> Result<Response<Self::StreamStream>, Status> {
        // router_service.go:84-88: authenticate first; failures are logged
        // and returned as the RPC status.
        let stream_name = match auth::authenticate(request.metadata(), &self.key) {
            Ok(name) => name,
            Err(status) => {
                tracing::error!(error = %status.message(), "failed to authenticate");
                return Err(status);
            }
        };

        info!(stream = %stream_name, "streaming");

        let inbound = request.into_inner();
        let (tx, rx) = mpsc::channel(RESPONSE_BUFFER);

        // The DashMap entry API gives the Go LoadOrStore atomicity: exactly
        // one of two racing peers for the same sub sees Vacant (and parks);
        // the other sees Occupied (and pairs). The shard lock is held only
        // for the synchronous branch bodies below.
        match self.pending.entry(stream_name.clone()) {
            Entry::Occupied(occupied) => {
                // Second peer: claim the waiter (Go LoadOrStore loaded=true +
                // CompareAndDelete, router_service.go:100-105 — the entry
                // removal and claim are one atomic step here).
                let (_, first) = occupied.remove_entry();
                // Stop the first peer's disconnect guard (Go `first.cancel()`
                // unparks the waiting handler; its RPC now lives until
                // forwarding ends).
                first.guard_cancel.cancel();
                let Some(first_peer) = first
                    .parked
                    .into_inner()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                else {
                    // Unreachable in production: every inserted entry parks a
                    // peer. (Constructible only by tests.)
                    return Err(Status::internal("pending entry has no parked peer"));
                };

                info!(stream = %stream_name, "forwarding");

                // Go: `return Forward(ctx, stream, first.stream)` — the
                // pairing peer's RPC ends with Forward's result; the waiting
                // peer's RPC ends cleanly when its response sender drops.
                let second_tx = tx.clone();
                tokio::spawn(async move {
                    let result =
                        forward::forward(inbound, &second_tx, first_peer.inbound, &first_peer.tx)
                            .await;
                    if let Err(status) = result {
                        // Deliver the first pipe error as the pairing peer's
                        // RPC status (ignore failure: that peer may already
                        // be gone, exactly like Go returning an error to a
                        // dead client).
                        let _ = second_tx.send(Err(status)).await;
                    }
                    // Dropping second_tx and first_peer.tx ends both response
                    // streams; the waiting peer sees a clean OK end.
                });
            }
            Entry::Vacant(vacant) => {
                // First peer: park and wait (Go LoadOrStore loaded=false,
                // router_service.go:109-113).
                let id = self.next_id.fetch_add(1, Ordering::Relaxed);
                let guard_cancel = CancellationToken::new();
                vacant.insert(PendingEntry {
                    id,
                    parked: Mutex::new(Some(ParkedPeer {
                        inbound,
                        tx: tx.clone(),
                    })),
                    guard_cancel: guard_cancel.clone(),
                });

                info!(stream = %stream_name, "waiting for the other side");

                // Disconnect guard, standing in for the Go waiting handler's
                // `defer s.pending.CompareAndDelete(streamName, sctx)` after
                // `<-ctx.Done()`: if the first peer's transport dies before a
                // partner arrives (tonic drops the response receiver, so
                // `tx.closed()` resolves), remove the entry — id-guarded so a
                // reconnect's fresh entry survives stale cleanup.
                let pending = Arc::clone(&self.pending);
                let watch_tx = tx.clone();
                tokio::spawn(async move {
                    tokio::select! {
                        _ = guard_cancel.cancelled() => {}
                        _ = watch_tx.closed() => {
                            remove_stale(&pending, &stream_name, id);
                        }
                    }
                });
            }
        }

        Ok(Response::new(Box::pin(ReceiverStream::new(rx))))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Fabricate an entry without a real gRPC stream (parked = None is
    /// test-only; production always parks a peer).
    fn fabricated_entry(id: u64) -> PendingEntry {
        PendingEntry {
            id,
            parked: Mutex::new(None),
            guard_cancel: CancellationToken::new(),
        }
    }

    /// The CompareAndDelete contract (`router_service.go:110`): stale cleanup
    /// removes the entry it belongs to, and never a newer entry under the
    /// same stream name.
    #[test]
    fn remove_stale_is_id_guarded() {
        let pending: Pending = DashMap::new();

        // Cleanup for the entry's own id removes it.
        pending.insert("stream-a".into(), fabricated_entry(7));
        assert!(remove_stale(&pending, "stream-a", 7));
        assert!(!pending.contains_key("stream-a"));

        // A reconnect replaced the entry: stale cleanup (old id) must no-op.
        pending.insert("stream-a".into(), fabricated_entry(8));
        assert!(!remove_stale(&pending, "stream-a", 7));
        assert!(pending.contains_key("stream-a"));

        // The newer entry's own cleanup still works.
        assert!(remove_stale(&pending, "stream-a", 8));
        assert!(!pending.contains_key("stream-a"));

        // Cleanup of a missing entry no-ops.
        assert!(!remove_stale(&pending, "stream-a", 8));
    }
}
