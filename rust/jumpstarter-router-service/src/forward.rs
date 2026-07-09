//! Verbatim frame forwarding between two paired `Stream` RPCs — the port of
//! `pipe` and `Forward` (`controller/internal/service/router_support.go`).
//!
//! Semantics reproduced exactly (spec 06 §3.3 and the errgroup notes in
//! `router_support.go:31-46`):
//!
//! - Two independent pipes, each `Recv`ing from one peer and `Send`ing to the
//!   other, copying `payload` and `frame_type` **verbatim** — zero-length
//!   DATA frames and unknown `frame_type` values included (Python PING
//!   semantics depend on the pass-through, spec 06 §2.3/§14).
//! - A pipe ends **only** on its own account: clean transport EOF from its
//!   source (the peer half-closed after sending its GOAWAY frame), a `Recv`
//!   error, or a `Send` error. Go's `pipe` never consults the errgroup
//!   context (`router_support.go:12-29` — no ctx select), so the first pipe
//!   error does NOT interrupt the opposite direction. This is what makes
//!   GOAWAY half-close work end-to-end — and it is also why a peer dying
//!   mid-forward leaves the survivor's RPC **open**: the survivor-side pipe
//!   stays blocked in `Recv` until the survivor itself half-closes, errors,
//!   or sends a frame whose relay to the dead peer fails (measured against
//!   the Go router — goldens b1–b4 in `tests/golden/router_behavior.json`
//!   record `still_open` survivors).
//! - [`forward`] returns only once **both** pipes have ended (Go: `Forward`
//!   returns `g.Wait()`, which joins both goroutines,
//!   `router_support.go:44-45`), yielding the chronologically-first error,
//!   if any (the errgroup "first non-nil error" contract). The caller —
//!   like the Go second-peer handler returning `Forward(...)`'s error —
//!   delivers it as the pairing peer's RPC status. The waiting peer's RPC
//!   always ends cleanly (Go: its handler returns `nil` after
//!   `<-ctx.Done()`).
//! - Pipe errors are delivered in **grpc-go's wire shape**, not tonic's: a
//!   dead peer's Recv error becomes CANCELLED "context canceled" and a
//!   failed relay to a dead peer becomes UNAVAILABLE "transport is closing"
//!   (measured against the Go router — goldens b1n/b3n in
//!   `tests/golden/router_behavior.json`; the survivor's status text is
//!   what the Python exporter retry classifier reads).

use jumpstarter_protocol::v1 as pb;
use tokio::sync::mpsc;
use tonic::{Status, Streaming};

/// The per-peer response channel. Ok items are forwarded frames; a final
/// Err item terminates that peer's RPC with the given status.
pub(crate) type ResponseTx = mpsc::Sender<Result<pb::StreamResponse, Status>>;

/// Response-channel depth. Go has **no** application-level buffering: each
/// pipe is a synchronous Recv→Send loop (`router_support.go:12-29`), so at
/// most ~1 frame per direction sits in the handler; everything else is
/// HTTP/2 flow control. The tonic handler model needs a channel between the
/// pipe and the response stream, but its bound is a memory-amplification
/// surface Go lacks (frames are up to the 4 MiB default message cap, so a
/// depth of N buffers up to 4N MiB per direction). Depth 2 keeps Go-shaped
/// backpressure — one frame queued while tonic encodes the previous, so the
/// pump never runs in strict lock-step — without meaningful amplification.
/// Wire-invisible; locked by `response_buffer_is_go_shaped` below and the
/// stalled-reader test in `tests/router.rs`.
pub(crate) const RESPONSE_BUFFER: usize = 2;

/// How one pipe direction ended.
enum PipeEnd {
    /// Clean transport EOF from the source (Go: `io.EOF` from `Recv`) —
    /// this direction is done; the other keeps flowing.
    Clean,
    /// Recv or Send failure; candidate for the first error.
    Error(Status),
}

/// Rewrites a tonic `Recv` transport error into the status the grpc-go
/// router puts on the wire for the same event.
///
/// When a peer dies mid-forward, grpc-go's parked `Recv` observes the dying
/// stream's context being cancelled and returns
/// `ContextErr(context.Canceled)` — CANCELLED `"context canceled"`
/// (grpc-go `internal/transport/transport.go` `recvBufferReader.read`) —
/// which `Forward` later hands to the pairing peer as its RPC status.
/// Measured against the Go router: golden `b3n_waiter_conn_sever_survivor_nudge`
/// in `tests/golden/router_behavior.json`. tonic instead surfaces the raw
/// transport error (`UNKNOWN "h2 protocol error: error reading a body from
/// connection"`) — text no grpc-go router ever emits, and the surviving
/// peer's status message is exactly what the Python exporter's retry
/// classifier reads. tonic marks this class with the `"h2 protocol error: "`
/// message prefix (`Status::from_h2_error`); anything else (e.g. local
/// decode/message-limit statuses) passes through verbatim.
fn recv_status_go_shape(status: Status) -> Status {
    if status.message().starts_with("h2 protocol error:") {
        Status::cancelled("context canceled")
    } else {
        status
    }
}

/// One direction: `Recv` from `src`, `Send` to `dst`, verbatim
/// (`router_support.go:12-29`). Runs until *this* direction ends — like the
/// Go `pipe`, it is never cancelled from outside, so an error on the
/// opposite pipe leaves this one blocked in `Recv` (the survivor's RPC
/// stays open, goldens b1–b4).
async fn pipe(src: &mut Streaming<pb::StreamRequest>, dst: &ResponseTx) -> PipeEnd {
    loop {
        match src.message().await {
            // Go: errors.Is(err, io.EOF) => return nil. Clean end of this
            // direction only; the other direction keeps flowing.
            Ok(None) => return PipeEnd::Clean,
            // Recv error: candidate for the Forward result, in grpc-go's
            // wire shape.
            Err(status) => return PipeEnd::Error(recv_status_go_shape(status)),
            Ok(Some(frame)) => {
                // payload + frame_type copied verbatim; `frame_type` is a raw
                // i32, so unknown enum values survive untouched.
                let out = pb::StreamResponse {
                    payload: frame.payload,
                    frame_type: frame.frame_type,
                };
                if dst.send(Ok(out)).await.is_err() {
                    // The destination RPC is gone (tonic dropped its response
                    // receiver). grpc-go's counterpart — `Send` on a stream
                    // whose client connection went away — fails with
                    // `ErrConnClosing`, and `toRPCErr` maps that to
                    // UNAVAILABLE "transport is closing". Measured against
                    // the Go router: golden
                    // `b1n_waiter_rst_cancel_survivor_nudge` (the killed
                    // waiter's inbound ends CLEAN, so this send failure is
                    // the first — and only — error, and the survivor's RPC
                    // ends with exactly this status). Caveat: grpc-go can
                    // also fail such a Send with CANCELLED "context
                    // canceled" when only the RPC (not the connection) was
                    // torn down; that sub-case is not distinguishable from
                    // an mpsc send failure and is not covered by a golden —
                    // the conn-close form is the measured contract.
                    return PipeEnd::Error(Status::unavailable("transport is closing"));
                }
            }
        }
    }
}

/// Runs one pipe and, on error, records it as the first error if none was
/// recorded yet — Go's errgroup keeps only the first non-nil result.
/// Deliberately does NOT stop the other pipe: Go's errgroup context
/// cancellation is invisible to `pipe` (it never selects on ctx), so the
/// opposite direction keeps flowing/blocking until it ends on its own.
async fn run_pipe(
    src: &mut Streaming<pb::StreamRequest>,
    dst: &ResponseTx,
    first_error: &std::sync::Mutex<Option<Status>>,
) {
    if let PipeEnd::Error(status) = pipe(src, dst).await {
        let mut slot = first_error.lock().unwrap_or_else(|e| e.into_inner());
        if slot.is_none() {
            *slot = Some(status);
        }
    }
}

/// Port of `Forward(ctx, a, b)` (`router_support.go:31-46`): pipes `a → b`
/// and `b → a` until **both** directions end (Go: `return g.Wait()` joins
/// both goroutines), returning the chronologically-first error (if any). By
/// Go convention `a` is the second (pairing) peer and `b` the first
/// (waiting) peer, but the forwarding itself is symmetric.
///
/// Callers deliver an `Err` into the pairing peer's response channel (the Go
/// second-peer handler returns it) and end the waiting peer's response
/// stream cleanly (the Go waiting handler returns `nil`).
pub(crate) async fn forward(
    mut a: Streaming<pb::StreamRequest>,
    a_tx: &ResponseTx,
    mut b: Streaming<pb::StreamRequest>,
    b_tx: &ResponseTx,
) -> Result<(), Status> {
    let first_error = std::sync::Mutex::new(None);

    // Both pipes polled concurrently within this task; join returns once
    // both directions have ended on their own (clean EOF, Recv error, or
    // Send-to-dead-peer error). A pipe error never interrupts the other
    // direction — matching Go, where the survivor's pipe keeps blocking in
    // Recv and the survivor's RPC stays open (goldens b1–b4: still_open).
    tokio::join!(
        run_pipe(&mut a, b_tx, &first_error),
        run_pipe(&mut b, a_tx, &first_error),
    );

    match first_error.into_inner().unwrap_or_else(|e| e.into_inner()) {
        Some(status) => Err(status),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Locks the response-channel depth to the Go backpressure shape: Go's
    /// pipe is a synchronous Recv→Send loop (`router_support.go:12-29`) with
    /// ~1 application-held frame per direction, so anything beyond a couple
    /// of frames here is a memory-amplification surface Go lacks (each frame
    /// may be 4 MiB; 16 would allow 64 MiB per direction). Raising this
    /// requires re-measuring against the Go router, not just editing the
    /// constant. Behavioral counterpart: `stalled_reader_backpressure_is_go_shaped`
    /// in `tests/router.rs`.
    #[test]
    fn response_buffer_is_go_shaped() {
        assert!(
            (1..=2).contains(&RESPONSE_BUFFER),
            "RESPONSE_BUFFER must stay at 1-2 frames to match Go's synchronous \
             Recv→Send backpressure (got {RESPONSE_BUFFER})"
        );
    }

    /// Locks the tonic→grpc-go status rewrite for a dead peer's Recv error
    /// (golden b3n: the Go survivor sees CANCELLED "context canceled", while
    /// tonic natively reports UNKNOWN "h2 protocol error: ..."). Non-h2
    /// statuses must pass through verbatim.
    #[test]
    fn recv_status_rewrites_only_h2_transport_errors() {
        // The exact status tonic produced for the severed-peer Recv in the
        // differential run (tests/differential.rs, scenario b3/b3n).
        let mapped = recv_status_go_shape(Status::unknown(
            "h2 protocol error: error reading a body from connection",
        ));
        assert_eq!(mapped.code(), tonic::Code::Cancelled);
        assert_eq!(mapped.message(), "context canceled");

        // Any h2-transport-flavored status maps, regardless of its code.
        let mapped = recv_status_go_shape(Status::cancelled(
            "h2 protocol error: stream error received: stream no longer needed",
        ));
        assert_eq!(mapped.code(), tonic::Code::Cancelled);
        assert_eq!(mapped.message(), "context canceled");

        // Locally-generated statuses (decode/message-limit) pass through.
        let passthrough = recv_status_go_shape(Status::out_of_range(
            "Error, message length too large: found 5000000 bytes, the limit is: 4194304 bytes",
        ));
        assert_eq!(passthrough.code(), tonic::Code::OutOfRange);
        assert!(passthrough.message().starts_with("Error, message length"));
    }
}
