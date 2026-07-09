//! Router-token delivery queues for the exporter `Listen` stream, a port of the
//! `listenQueues` machinery in
//! `controller/internal/service/controller_service.go` (~91-195, 580-603).
//!
//! # The invariant we preserve
//!
//! `Dial` (client side) mints a router `(endpoint, token)` pair and must hand it
//! to the exporter's live `Listen` goroutine so both peers rendezvous on the
//! router. When an exporter reconnects, a fresh `Listen` supersedes the old one;
//! the contract is **"Dial never sends a token to a superseded queue"** and
//! **"tokens already buffered for a superseded listener are still delivered to
//! that (old) exporter connection before it exits"** (lossless reconnect).
//!
//! # How the Go version does it, and how we differ
//!
//! Go uses three primitives — a `sync.Map` of queues, a *second* `sync.Map` of
//! reference-counted per-lease `sync.Mutex`es, and a per-queue `done` channel +
//! `sync.Once` — so that the load-then-send in `sendToListener` is atomic with
//! respect to the swap-then-close in `swapListenQueue`.
//!
//! We collapse that to a **single [`std::sync::Mutex`] over a `HashMap`**. Every
//! registry mutation (`register`/`send_to_listener`/`deregister`) takes that one
//! lock, so the load+send in [`ListenRegistry::send_to_listener`] is trivially
//! atomic with the insert+cancel in [`ListenRegistry::register`] — the exact
//! TOCTOU guarantee the Go ref-counted mutex existed to provide, without the
//! ref-counting dance. The per-queue `done`/`closeOnce` become a
//! [`CancellationToken`] (cancellation is idempotent and atomic), and Go's
//! pointer-identity `CompareAndDelete` becomes a monotonic `epoch` guard.
//!
//! `send_to_listener` uses [`mpsc::Sender::try_send`], which never awaits, so
//! holding the mutex across it is sound (and, per the Go comment at
//! controller_service.go:187, *avoids* the deadlock where a blocking send would
//! pin the mutex and stall a reconnecting `Listen`'s swap).

use std::collections::HashMap;
use std::sync::Mutex;

use jumpstarter_protocol::v1::ListenResponse;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

/// Buffered router tokens per listener. **Contractual** (spec 02 §7.4): the Go
/// channel is `make(chan *pb.ListenResponse, 8)` and clients/tests depend on the
/// buffer-full → `ResourceExhausted` boundary landing at exactly the 9th
/// undelivered token.
pub const LISTEN_QUEUE_CAPACITY: usize = 8;

/// The active listener queue for one lease. Mirrors Go's `listenQueue`:
/// `tx`/`rx` are the buffered channel, `done` is the supersede/exit signal
/// (Go's `done chan struct{}` + `closeOnce`), and `epoch` replaces Go's
/// pointer-identity check for [`ListenRegistry::deregister`].
struct ActiveQueue {
    /// Sender half; the matching [`mpsc::Receiver`] is owned by the `Listen`
    /// handler via the [`Registration`] returned from [`ListenRegistry::register`].
    tx: mpsc::Sender<ListenResponse>,
    /// Cancelled when this queue is superseded by a reconnecting `Listen`
    /// (in `register`) or torn down by its own handler (in `deregister`).
    done: CancellationToken,
    /// Monotonic identity of this registration; [`ListenRegistry::deregister`]
    /// removes the map entry only if the stored epoch still matches, so a
    /// reconnecting listener's fresh entry survives the old handler's cleanup.
    epoch: u64,
}

/// Handle returned to a `Listen` handler by [`ListenRegistry::register`]: the
/// receiving end of its private queue, the supersede/exit signal, and the epoch
/// it must pass back to [`ListenRegistry::deregister`] on exit.
///
/// (Equivalent to the task's `(Receiver, CancellationToken, epoch)` tuple; a
/// named struct keeps the call sites and the [`drive_listen_loop`] destructure
/// readable.)
pub struct Registration {
    /// Receives router tokens delivered by `Dial` via
    /// [`ListenRegistry::send_to_listener`]. Each registration gets a *fresh*
    /// channel, so a reconnecting exporter never shares a buffer with the old
    /// handler (Go comment at controller_service.go:563-565).
    pub rx: mpsc::Receiver<ListenResponse>,
    /// Cancelled when this listener is superseded or asked to exit.
    pub done: CancellationToken,
    /// Pass this to [`ListenRegistry::deregister`] on handler exit.
    pub epoch: u64,
}

/// Registry of active exporter `Listen` queues keyed by lease name.
///
/// One [`Mutex`] guards the whole map; see the module docs for why that single
/// lock is sufficient (and preferable) to Go's three-primitive scheme.
#[derive(Default)]
pub struct ListenRegistry {
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    queues: HashMap<String, ActiveQueue>,
    /// Source of monotonically increasing [`Registration::epoch`] values.
    next_epoch: u64,
}

impl ListenRegistry {
    /// Create an empty registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a fresh queue for `lease`, superseding any previous listener.
    ///
    /// Port of `swapListenQueue` (controller_service.go:147-156): under the
    /// lock, insert the new queue and **cancel the previous queue's token**, so
    /// the old `Listen` handler observes supersession and drains + exits. The
    /// caller (the `Listen` RPC handler) drives [`drive_listen_loop`] with the
    /// returned [`Registration`] and calls [`Self::deregister`] on exit.
    pub fn register(&self, lease: &str) -> Registration {
        let (tx, rx) = mpsc::channel(LISTEN_QUEUE_CAPACITY);
        let done = CancellationToken::new();

        let mut inner = self.lock();
        let epoch = inner.next_epoch;
        inner.next_epoch += 1;
        let new_queue = ActiveQueue {
            tx,
            done: done.clone(),
            epoch,
        };
        // Swap in ours; signal the superseded queue (if any) to stop.
        if let Some(old) = inner.queues.insert(lease.to_string(), new_queue) {
            old.done.cancel();
        }
        drop(inner);

        Registration { rx, done, epoch }
    }

    /// Deliver a router token to the active listener for `lease`.
    ///
    /// Port of `sendToListener` (controller_service.go:171-195). Called by
    /// `Dial`. The whole load+check+send runs under the single registry lock, so
    /// the queue read here cannot be superseded mid-send:
    ///
    /// - no entry, or the entry's `done` is already cancelled ⇒
    ///   [`tonic::Status::unavailable`] `"exporter is not listening on lease
    ///   {lease}"`;
    /// - buffer full (`try_send` returns `Full`) ⇒
    ///   [`tonic::Status::resource_exhausted`] `"listener buffer full on lease
    ///   {lease}"` — never blocks while holding the lock;
    /// - a closed channel (receiver dropped without a clean `deregister`) is
    ///   treated as "not listening" ⇒ `Unavailable`.
    ///
    /// Mirrors Go, the context argument is intentionally absent: Go's
    /// `sendToListener` takes `_ context.Context` and never consults it — the
    /// send is bounded by the buffer, not the caller's deadline.
    // `tonic::Status` is a large Err type, but every gRPC handler in this crate
    // returns it — boxing here would just diverge from the rest of the surface.
    #[allow(clippy::result_large_err)]
    pub fn send_to_listener(&self, lease: &str, msg: ListenResponse) -> Result<(), tonic::Status> {
        let inner = self.lock();
        let Some(queue) = inner.queues.get(lease) else {
            return Err(not_listening(lease));
        };
        // Reject if this queue was already superseded/torn down.
        if queue.done.is_cancelled() {
            return Err(not_listening(lease));
        }
        // Non-blocking: holding the mutex while awaiting a drain would stall a
        // reconnecting Listen's swap (Go controller_service.go:187-194).
        match queue.tx.try_send(msg) {
            Ok(()) => Ok(()),
            Err(mpsc::error::TrySendError::Full(_)) => Err(tonic::Status::resource_exhausted(
                format!("listener buffer full on lease {lease}"),
            )),
            Err(mpsc::error::TrySendError::Closed(_)) => Err(not_listening(lease)),
        }
    }

    /// Remove `lease`'s queue **only if** its epoch still matches `epoch`.
    ///
    /// Port of the `s.listenQueues.CompareAndDelete(leaseName, wrapper)` in the
    /// `Listen` defer (controller_service.go:577): a reconnecting listener will
    /// have replaced the entry with a fresh epoch, so the old handler's cleanup
    /// must be a no-op against it. Also cancels the entry's `done` when it does
    /// remove, so a `Dial` racing the teardown sees `Unavailable` rather than
    /// buffering a token into a dead queue (Go closes `done` before the delete).
    pub fn deregister(&self, lease: &str, epoch: u64) {
        let mut inner = self.lock();
        if let Some(queue) = inner.queues.get(lease) {
            if queue.epoch == epoch {
                let removed = inner.queues.remove(lease).expect("entry present");
                removed.done.cancel();
            }
        }
    }

    #[cfg(test)]
    fn contains(&self, lease: &str) -> bool {
        self.lock().queues.contains_key(lease)
    }

    #[cfg(test)]
    fn epoch_of(&self, lease: &str) -> Option<u64> {
        self.lock().queues.get(lease).map(|q| q.epoch)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        // We never panic while holding this lock, so poisoning cannot occur;
        // treat a poisoned lock as a fatal invariant break.
        self.inner.lock().expect("listen registry mutex poisoned")
    }
}

fn not_listening(lease: &str) -> tonic::Status {
    tonic::Status::unavailable(format!("exporter is not listening on lease {lease}"))
}

/// The reusable body of the exporter `Listen` stream handler.
///
/// Port of the `for { select { ... } }` loop in `Listen`
/// (controller_service.go:580-603) plus its `defer` cleanup. The `Listen` RPC
/// handler in `controller_service.rs` should: authenticate + validate the lease,
/// call [`ListenRegistry::register`], build the outbound response channel
/// (backing a `ReceiverStream`), then run this loop.
///
/// `outbound` is the sender feeding the gRPC response stream. Its
/// [`mpsc::Sender::closed`] future resolves when the client (exporter) goes
/// away — this is our equivalent of Go's `<-ctx.Done()`.
///
/// Behaviour, arm for arm:
/// - **client gone** (`outbound.closed()`): return immediately, no drain — Go's
///   `case <-ctx.Done(): return nil`.
/// - **superseded** (`done.cancelled()`): drain every already-buffered token via
///   `try_recv` to the **old** `outbound` stream, then return — Go's
///   `case <-wrapper.done:` drain loop, preserving lossless reconnect.
/// - **token arrived** (`rx.recv()`): forward it; a send error (client gone)
///   ends the loop, matching Go's `if err := stream.Send(msg); err != nil`.
///
/// On exit it runs the Go `defer`: cancel our own `done` (idempotent) then
/// [`ListenRegistry::deregister`] with our epoch (epoch-guarded, so a
/// reconnect's entry is untouched).
pub async fn drive_listen_loop(
    registry: &ListenRegistry,
    lease: &str,
    registration: Registration,
    outbound: mpsc::Sender<Result<ListenResponse, tonic::Status>>,
) {
    let Registration {
        mut rx,
        done,
        epoch,
    } = registration;

    loop {
        tokio::select! {
            // Client (exporter) disconnected: Go `case <-ctx.Done(): return nil`.
            _ = outbound.closed() => break,

            // Superseded by a reconnecting Listen: drain buffered tokens to the
            // OLD exporter connection so none are lost, then exit.
            _ = done.cancelled() => {
                while let Ok(msg) = rx.try_recv() {
                    // Best-effort: if the old stream is already gone, stop.
                    if outbound.send(Ok(msg)).await.is_err() {
                        break;
                    }
                }
                break;
            }

            // A router token arrived from Dial: forward it to the exporter.
            recv = rx.recv() => match recv {
                Some(msg) => {
                    if outbound.send(Ok(msg)).await.is_err() {
                        break; // client gone mid-send
                    }
                }
                // Sender dropped without cancelling done (shouldn't happen in
                // normal flow, since the map holds the only Sender); exit.
                None => break,
            },
        }
    }

    // Go defer: closeDone() then CompareAndDelete(leaseName, wrapper).
    done.cancel();
    registry.deregister(lease, epoch);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicI64, Ordering};
    use std::sync::Arc;
    use tokio::sync::mpsc;

    fn token(endpoint: &str, tok: &str) -> ListenResponse {
        ListenResponse {
            router_endpoint: endpoint.to_string(),
            router_token: tok.to_string(),
        }
    }

    const TOK: &str = "tok";

    // ---- register / deregister epoch-guarded (CompareAndDelete) ----

    // go: controller_service_test.go:351 TestListenQueueCompareAndDeleteOnCleanShutdown
    #[tokio::test]
    async fn deregister_removes_on_clean_shutdown() {
        let reg = ListenRegistry::new();
        let r = reg.register("l");
        reg.deregister("l", r.epoch);
        assert!(
            !reg.contains("l"),
            "queue should be removed on clean shutdown"
        );
    }

    // go: controller_service_test.go:320 TestListenQueueCompareAndDeleteOnStreamError
    #[tokio::test]
    async fn deregister_survives_when_reconnect_replaced_entry() {
        let reg = ListenRegistry::new();
        let first = reg.register("l");
        let second = reg.register("l"); // reconnect supersedes

        // Old handler's cleanup must be a no-op against the reconnected entry.
        reg.deregister("l", first.epoch);

        assert!(
            reg.contains("l"),
            "reconnected queue must survive stale cleanup"
        );
        assert_eq!(reg.epoch_of("l"), Some(second.epoch));
    }

    // go: controller_service_test.go:432 TestListenQueueReconnectPreventsStaleCleanup
    #[tokio::test]
    async fn stale_cleanup_does_not_disturb_reconnected_queue() {
        let reg = ListenRegistry::new();
        let original = reg.register("l");
        let _reconnect = reg.register("l");

        reg.deregister("l", original.epoch); // no-op

        assert!(reg.contains("l"));
        // The reconnected listener still receives tokens.
        reg.send_to_listener("l", token("ep", TOK)).unwrap();
    }

    // ---- supersession signalling ----

    // go: controller_service_test.go:365 TestListenQueueReconnectCreatesNewChannel
    // go: controller_service_test.go:929 TestListenQueueSupersessionSignaling
    #[tokio::test]
    async fn reconnect_cancels_old_and_installs_new() {
        let reg = ListenRegistry::new();
        let first = reg.register("l");
        let second = reg.register("l");

        assert!(
            first.done.is_cancelled(),
            "old done should be cancelled after swap"
        );
        assert!(!second.done.is_cancelled(), "new done should be open");
        assert_ne!(first.epoch, second.epoch);
        assert_eq!(reg.epoch_of("l"), Some(second.epoch));
    }

    // go: controller_service_test.go:472 TestListenQueueConcurrentSwapSupersedes
    #[tokio::test]
    async fn three_way_supersession() {
        let reg = ListenRegistry::new();
        let g1 = reg.register("l");
        let g2 = reg.register("l");
        let g3 = reg.register("l");

        assert!(g1.done.is_cancelled());
        assert!(g2.done.is_cancelled());
        assert!(!g3.done.is_cancelled());

        // g1/g2 deferred cleanups are no-ops.
        reg.deregister("l", g1.epoch);
        reg.deregister("l", g2.epoch);
        assert_eq!(reg.epoch_of("l"), Some(g3.epoch));
    }

    // go: controller_service_test.go:905 TestListenQueueDoneClosedOnNormalExit
    #[tokio::test]
    async fn cancel_is_idempotent() {
        let reg = ListenRegistry::new();
        let r = reg.register("l");
        r.done.cancel();
        assert!(r.done.is_cancelled());
        r.done.cancel();
        assert!(r.done.is_cancelled());
    }

    // ---- send_to_listener delivery + supersession ----

    // go: controller_service_test.go:1361 TestListenQueueDialFlowSendsToActiveListener
    // go: controller_service_test.go:678 TestDialSendsTokenViaServiceMethod
    #[tokio::test]
    async fn send_delivers_to_active_listener() {
        let reg = ListenRegistry::new();
        let mut r = reg.register("l");
        reg.send_to_listener("l", token("dial-ep", "dial-tok"))
            .unwrap();
        let got = r.rx.try_recv().expect("token delivered");
        assert_eq!(got.router_endpoint, "dial-ep");
        assert_eq!(got.router_token, "dial-tok");
    }

    // go: controller_service_test.go:400 TestListenQueueDialTokenDeliveredToNewListener
    // go: controller_service_test.go:517 TestListenQueueStaleReaderConsumesDialToken
    // go: controller_service_test.go:705 TestDialSendToListenerRejectsSupersededQueue
    #[tokio::test]
    async fn send_goes_to_new_listener_not_old() {
        let reg = ListenRegistry::new();
        let mut g1 = reg.register("l");
        let mut g2 = reg.register("l");

        reg.send_to_listener("l", token("ep", "test-token"))
            .unwrap();

        assert!(
            g1.rx.try_recv().is_err(),
            "old listener must not receive the token"
        );
        let got = g2.rx.try_recv().expect("new listener receives the token");
        assert_eq!(got.router_token, "test-token");
        assert!(g1.done.is_cancelled(), "old done cancelled after swap");
    }

    // go: controller_service_test.go:744 TestDialSendToListenerRejectsNoListener
    // go: controller_service_test.go:1032 TestListenQueueDialReturnsUnavailableWhenNoListener
    #[tokio::test]
    async fn send_with_no_listener_is_unavailable() {
        let reg = ListenRegistry::new();
        let err = reg
            .send_to_listener("nonexistent", token("ep", TOK))
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unavailable);
        assert!(err
            .message()
            .contains("exporter is not listening on lease nonexistent"));
    }

    // go: controller_service_test.go:754 TestDialSendToListenerRejectsDoneQueue
    // go: controller_service_test.go:1051 TestListenQueueDialReturnsUnavailableWhenDoneClosed
    // go: controller_service_test.go:601 TestDialRejectsSupersededQueue
    #[tokio::test]
    async fn send_to_cancelled_queue_is_unavailable() {
        let reg = ListenRegistry::new();
        let r = reg.register("l");
        r.done.cancel(); // simulate the handler tearing down

        let err = reg.send_to_listener("l", token("ep", TOK)).unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unavailable);
        // Nothing should have been buffered.
        let mut r = r;
        assert!(r.rx.try_recv().is_err());
    }

    // go: controller_service_test.go:1005 TestListenQueueDoneClosedBeforeMapDeleteWithConcurrentDial
    #[tokio::test]
    async fn cancel_before_delete_rejects_then_delete_is_noop_for_stale_epoch() {
        let reg = ListenRegistry::new();
        let r = reg.register("l");
        r.done.cancel();
        assert!(reg.send_to_listener("l", token("ep", TOK)).is_err());
        reg.deregister("l", r.epoch);
        assert!(!reg.contains("l"));
    }

    // ---- buffer-full: ResourceExhausted, non-blocking ----

    // go: controller_service_test.go:1550 TestSendToListenerReturnsResourceExhaustedWhenBufferFull
    // go: controller_service_test.go:1331 TestSendToListenerReturnsImmediatelyDuringBackpressure
    #[tokio::test]
    async fn buffer_full_is_resource_exhausted() {
        let reg = ListenRegistry::new();
        let _r = reg.register("l"); // keep rx alive but never drain
        for _ in 0..LISTEN_QUEUE_CAPACITY {
            reg.send_to_listener("l", token("fill", "fill")).unwrap();
        }
        let err = reg.send_to_listener("l", token("ep", TOK)).unwrap_err();
        assert_eq!(err.code(), tonic::Code::ResourceExhausted);
        assert!(err.message().contains("listener buffer full on lease l"));
    }

    // go: controller_service_test.go:1580 TestSendToListenerDoesNotBlockMutexWhenBufferFull
    // go: controller_service_test.go:1629 TestSwapNotBlockedWhenBufferFull
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn full_buffer_does_not_block_swap() {
        let reg = Arc::new(ListenRegistry::new());
        let g1 = reg.register("l");
        for _ in 0..LISTEN_QUEUE_CAPACITY {
            reg.send_to_listener("l", token("fill", "fill")).unwrap();
        }
        // Send on a full buffer returns immediately (ResourceExhausted).
        let err = reg.send_to_listener("l", token("ep", TOK)).unwrap_err();
        assert_eq!(err.code(), tonic::Code::ResourceExhausted);

        // A reconnecting Listen's swap must not be blocked by the full buffer.
        let reg2 = reg.clone();
        let swap = tokio::spawn(async move {
            reg2.register("l");
        });
        tokio::time::timeout(std::time::Duration::from_secs(2), swap)
            .await
            .expect("swap must not block on a full buffer")
            .unwrap();

        assert!(g1.done.is_cancelled(), "g1 done cancelled after swap");
    }

    // ---- concurrency: swap races send, token never lands on superseded ----

    // go: controller_service_test.go:824 TestDialSendToListenerConcurrentWithSwapNeverLandsOnSuperseded
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn concurrent_swap_and_send_never_duplicates_or_loses() {
        let iterations = 500;
        let mut sent_to_g1 = 0;
        let mut sent_to_g2 = 0;
        let mut rejected = 0;

        for _ in 0..iterations {
            let reg = Arc::new(ListenRegistry::new());
            let mut g1 = reg.register("l");

            let reg_swap = reg.clone();
            let swap = tokio::spawn(async move { reg_swap.register("l") });
            let reg_send = reg.clone();
            let send =
                tokio::spawn(async move { reg_send.send_to_listener("l", token("ep", TOK)) });

            let mut g2 = swap.await.unwrap();
            let send_res = send.await.unwrap();

            if send_res.is_err() {
                rejected += 1;
                continue;
            }

            let on_g1 = g1.rx.try_recv().is_ok();
            let on_g2 = g2.rx.try_recv().is_ok();
            if on_g1 {
                sent_to_g1 += 1;
            }
            if on_g2 {
                sent_to_g2 += 1;
            }
            assert!(on_g1 || on_g2, "send succeeded but token was lost");
            assert!(!(on_g1 && on_g2), "token duplicated across queues");
        }
        assert_eq!(sent_to_g1 + sent_to_g2 + rejected, iterations);
    }

    // go: controller_service_test.go:635 TestDialWithPreSwapReferenceNeverSendsToStaleQueue
    // go: controller_service_test.go:778 TestDialSendToListenerSerializesWithSwap
    #[tokio::test]
    async fn serialized_swap_then_send_hits_new_queue() {
        for _ in 0..500 {
            let reg = ListenRegistry::new();
            let mut g1 = reg.register("l");
            let mut g2 = reg.register("l");
            reg.send_to_listener("l", token("ep", TOK)).unwrap();
            assert!(
                g1.rx.try_recv().is_err(),
                "token delivered to superseded g1"
            );
            assert!(g2.rx.try_recv().is_ok(), "token not delivered to active g2");
        }
    }

    // go: controller_service_test.go:561 TestListenQueueStaleReaderAlwaysDetectsSupersession
    #[tokio::test]
    async fn stale_reader_always_detects_supersession() {
        for _ in 0..100 {
            let reg = ListenRegistry::new();
            let g1 = reg.register("l");
            let mut g1_rx = g1.rx;
            let _g2 = reg.register("l");
            reg.send_to_listener("l", token("ep", TOK)).unwrap();
            assert!(g1.done.is_cancelled());
            assert!(g1_rx.try_recv().is_err(), "stale reader consumed a token");
        }
    }

    // ---- drive_listen_loop: forward, drain-on-supersession, client-gone ----

    // go: controller_service_test.go:1229 TestListenQueueListenLoopDeliversTokensAndExitsOnDone
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn loop_delivers_tokens_then_exits_on_supersession() {
        let reg = Arc::new(ListenRegistry::new());
        let registration = reg.register("l");
        let (out_tx, mut out_rx) = mpsc::channel(16);

        let reg2 = reg.clone();
        let handle = tokio::spawn(async move {
            drive_listen_loop(&reg2, "l", registration, out_tx).await;
        });

        reg.send_to_listener("l", token("ep1", "tok1")).unwrap();
        reg.send_to_listener("l", token("ep2", "tok2")).unwrap();

        for want in ["tok1", "tok2"] {
            let got = tokio::time::timeout(std::time::Duration::from_secs(1), out_rx.recv())
                .await
                .expect("token delivered in time")
                .expect("stream open")
                .expect("ok token");
            assert_eq!(got.router_token, want);
        }

        // Supersede: the loop must drain and exit.
        let superseder = reg.register("l");
        tokio::time::timeout(std::time::Duration::from_secs(1), handle)
            .await
            .expect("loop exits after supersession")
            .unwrap();

        assert_eq!(reg.epoch_of("l"), Some(superseder.epoch));
    }

    // go: controller_service_test.go:1688 TestListenQueueDrainsBufferedTokensOnSupersession
    // go: controller_service_test.go:1733 TestListenQueueListenLoopDrainsOnSupersession
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn loop_drains_buffered_tokens_to_old_stream_on_supersession() {
        let reg = Arc::new(ListenRegistry::new());
        let registration = reg.register("l");

        // Buffer two tokens BEFORE the loop starts consuming.
        reg.send_to_listener("l", token("ep1", "tok1")).unwrap();
        reg.send_to_listener("l", token("ep2", "tok2")).unwrap();

        // Supersede so the loop's first action is the drain branch.
        let _superseder = reg.register("l");
        assert!(registration.done.is_cancelled());

        let (out_tx, mut out_rx) = mpsc::channel(16);
        let reg2 = reg.clone();
        let handle = tokio::spawn(async move {
            drive_listen_loop(&reg2, "l", registration, out_tx).await;
        });

        tokio::time::timeout(std::time::Duration::from_secs(1), handle)
            .await
            .expect("loop drains and exits")
            .unwrap();

        // Both buffered tokens must have reached the old stream.
        let mut drained = Vec::new();
        while let Ok(Some(Ok(msg))) = out_rx.try_recv().map(Some) {
            drained.push(msg);
            if drained.len() == 2 {
                break;
            }
        }
        assert_eq!(
            drained.len(),
            2,
            "both buffered tokens drained to old stream"
        );
    }

    // go: controller_service_test.go:1077 TestListenQueueContextCancellationExitsListenLoop
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn loop_exits_when_client_disconnects() {
        let reg = Arc::new(ListenRegistry::new());
        let registration = reg.register("l");
        let epoch = registration.epoch;
        let (out_tx, out_rx) = mpsc::channel::<Result<ListenResponse, tonic::Status>>(16);

        let reg2 = reg.clone();
        let handle = tokio::spawn(async move {
            drive_listen_loop(&reg2, "l", registration, out_tx).await;
        });

        // Client goes away: drop the receiving end of the response stream.
        drop(out_rx);

        tokio::time::timeout(std::time::Duration::from_secs(1), handle)
            .await
            .expect("loop exits on client disconnect")
            .unwrap();

        // On exit the loop deregistered its own (unsuperseded) epoch.
        assert_eq!(reg.epoch_of("l"), None);
        let _ = epoch;
    }

    /// Spawn a draining listener for a [`Registration`]: it counts delivered
    /// tokens into `delivered` until its `done` is cancelled, then returns the
    /// number of tokens still buffered (the "drained" remainder). This mirrors
    /// the Go test's per-goroutine drain accounting.
    fn spawn_drainer(
        registration: Registration,
        delivered: Arc<AtomicI64>,
    ) -> tokio::task::JoinHandle<i64> {
        let Registration { mut rx, done, .. } = registration;
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = done.cancelled() => break,
                    m = rx.recv() => match m {
                        Some(_) => { delivered.fetch_add(1, Ordering::SeqCst); }
                        None => break,
                    }
                }
            }
            let mut drained = 0i64;
            while rx.try_recv().is_ok() {
                drained += 1;
            }
            drained
        })
    }

    // go: controller_service_test.go:1108 TestListenQueueConcurrentDialDuringReconnection
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn concurrent_dial_during_reconnection_accounts_for_every_token() {
        let reg = Arc::new(ListenRegistry::new());
        let delivered = Arc::new(AtomicI64::new(0));
        let sent = Arc::new(AtomicI64::new(0));
        let rejected = Arc::new(AtomicI64::new(0));

        // g1 is the initial listener; its done is cancelled automatically when
        // g2 supersedes it, so its drainer exits on its own.
        let g1 = reg.register("l");
        let g1_listener = spawn_drainer(g1, delivered.clone());

        let dial_attempts = 50i64;
        let mut dials = Vec::new();
        // g2's drainer must be stopped manually (nothing supersedes it).
        let mut g2_done: Option<CancellationToken> = None;
        let mut g2_listener = None;

        for i in 0..dial_attempts {
            let reg_d = reg.clone();
            let sent_d = sent.clone();
            let rejected_d = rejected.clone();
            dials.push(tokio::spawn(async move {
                match reg_d.send_to_listener("l", token("ep", TOK)) {
                    Ok(()) => sent_d.fetch_add(1, Ordering::SeqCst),
                    Err(_) => rejected_d.fetch_add(1, Ordering::SeqCst),
                };
            }));

            if i == 25 {
                // Reconnect mid-flight: supersede g1.
                let g2 = reg.register("l");
                g2_done = Some(g2.done.clone());
                g2_listener = Some(spawn_drainer(g2, delivered.clone()));
            }
        }

        for d in dials {
            d.await.unwrap();
        }

        // g1 was superseded at i==25 → its drainer already exited (or will once
        // it observes the cancel); await its drained remainder.
        let g1_drained = g1_listener.await.unwrap();

        // Stop g2 and collect its drained remainder.
        let g2_drained = if let (Some(done), Some(handle)) = (g2_done, g2_listener) {
            done.cancel();
            handle.await.unwrap()
        } else {
            0
        };

        let delivered_total = delivered.load(Ordering::SeqCst);
        let rejected_total = rejected.load(Ordering::SeqCst);
        let sent_total = sent.load(Ordering::SeqCst);
        let drained_total = g1_drained + g2_drained;

        // Every dial is accounted for: delivered to a listener, still buffered
        // (drained), or rejected — none lost, none duplicated.
        assert_eq!(
            delivered_total + drained_total + rejected_total,
            dial_attempts,
            "delivered {delivered_total} + drained {drained_total} + rejected {rejected_total} != {dial_attempts}"
        );
        // Every successful send was either delivered or left buffered.
        assert_eq!(sent_total, delivered_total + drained_total);
    }
}
