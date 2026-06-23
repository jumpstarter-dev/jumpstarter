//! The signal mailbox: the seam between the async world and the pure FSM.
//!
//! External inputs are delivered as *facts* (`Signal`), never as transition commands, each in
//! an [`Envelope`] that optionally carries a one-shot reply so the sender can *observe the
//! outcome* of its signal (`Committed` / `Ignored` / `Rejected`). [`SignalSink`] lifts a
//! source-specific signal type into the machine's signal enum, so an effect handed a
//! `SignalSink<HookSignal, _>` can only emit hook-origin facts — the origin of every signal is
//! enforced at the type level.

use std::marker::PhantomData;

use tokio::sync::{mpsc, oneshot};

/// Why a [`CommitGuard`] refused a candidate transition (e.g. the controller rejected the
/// projected status report — DD-7).
#[derive(Clone, Debug)]
pub struct RejectReason(pub String);

/// Observable result of applying one signal — the reply to an [`Envelope`].
#[derive(Clone, Debug)]
pub enum Outcome {
    /// The signal drove a state change that was adopted.
    Committed,
    /// The signal was irrelevant to the current state — no change.
    Ignored,
    /// A commit-guard refused a status-changing candidate; the state did not advance.
    Rejected(RejectReason),
}

/// A signal plus an optional one-shot reply channel to observe its [`Outcome`].
pub struct Envelope<Sig> {
    pub signal: Sig,
    pub reply: Option<oneshot::Sender<Outcome>>,
}

impl<Sig> Envelope<Sig> {
    /// A fire-and-forget envelope (no outcome observation).
    pub fn new(signal: Sig) -> Self {
        Self {
            signal,
            reply: None,
        }
    }

    /// An envelope whose outcome the caller awaits on the returned receiver.
    pub fn with_reply(signal: Sig) -> (Self, oneshot::Receiver<Outcome>) {
        let (tx, rx) = oneshot::channel();
        (
            Self {
                signal,
                reply: Some(tx),
            },
            rx,
        )
    }
}

/// Deliver an outcome to a waiting sender, if any. Free-standing so a runner can destructure
/// `Envelope { signal, reply }`, compute the outcome from `signal`, then `ack(reply, outcome)`.
pub fn ack(reply: Option<oneshot::Sender<Outcome>>, outcome: Outcome) {
    if let Some(tx) = reply {
        let _ = tx.send(outcome);
    }
}

/// The receiving end of a machine's mailbox.
pub struct Mailbox<Sig> {
    rx: mpsc::UnboundedReceiver<Envelope<Sig>>,
}

impl<Sig> Mailbox<Sig> {
    /// Create a connected `(sender, mailbox)` pair.
    pub fn channel() -> (mpsc::UnboundedSender<Envelope<Sig>>, Mailbox<Sig>) {
        let (tx, rx) = mpsc::unbounded_channel();
        (tx, Mailbox { rx })
    }

    /// Receive the next envelope. Cancel-safe. Returns `None` once all senders are dropped.
    pub async fn recv(&mut self) -> Option<Envelope<Sig>> {
        self.rx.recv().await
    }
}

/// A typed sink that lifts a source-specific signal `T` into the machine's signal `Sig` and
/// posts it (fire-and-forget) to the mailbox. Because an effect is only ever handed a sink for
/// its own origin, it is *compile-impossible* for, say, a hook task to emit a controller
/// signal.
pub struct SignalSink<T, Sig> {
    tx: mpsc::UnboundedSender<Envelope<Sig>>,
    _origin: PhantomData<fn(T)>,
}

impl<T, Sig> Clone for SignalSink<T, Sig> {
    fn clone(&self) -> Self {
        Self {
            tx: self.tx.clone(),
            _origin: PhantomData,
        }
    }
}

impl<T, Sig> SignalSink<T, Sig>
where
    T: Into<Sig>,
{
    /// Wrap a mailbox sender as an origin-typed sink for `T`.
    pub fn new(tx: mpsc::UnboundedSender<Envelope<Sig>>) -> Self {
        Self {
            tx,
            _origin: PhantomData,
        }
    }

    /// Post a source-specific fact. Returns `false` if the mailbox is closed.
    pub fn send(&self, signal: T) -> bool {
        self.tx.send(Envelope::new(signal.into())).is_ok()
    }
}

/// An async pre-commit gate over a wrapper state `W`. The default behaviour (no guard) is to
/// commit every candidate; the exporter supplies one that reports the projected status to the
/// controller and refuses the transition if it is rejected (DD-7).
pub trait CommitGuard<W>: Send {
    /// Decide whether the transition `from -> to` may be adopted.
    fn check(
        &mut self,
        from: &W,
        to: &W,
    ) -> impl std::future::Future<Output = Result<(), RejectReason>> + Send;
}
