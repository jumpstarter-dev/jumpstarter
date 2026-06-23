//! Typestate finite-state-machine core for the Jumpstarter runtime lifecycles.
//!
//! - [`Handle`] is the typestate carrier (context + type-level state); transitions consume
//!   `self` and return the *concrete* successor handle, so an illegal transition does not
//!   type-check.
//! - [`Fsm`] is the runtime face the wrapper enum implements (via `#[derive(StateMachine)]`):
//!   `apply` routes a signal to the current state, and `drive` gives a pure async iterator.
//! - The mailbox ([`Envelope`], [`Mailbox`], [`SignalSink`], [`Outcome`], [`CommitGuard`]) is
//!   the seam to the async world: external inputs arrive as origin-typed *facts*, and each
//!   carries an optional reply so a sender can observe its outcome.
//!
//! This crate is pure and binding-agnostic — it must not depend on tonic, the wire protocol,
//! or tokio process/net features.

mod fsm;
mod mailbox;

pub use fsm::{Fsm, Handle, Live};
pub use mailbox::{ack, CommitGuard, Envelope, Mailbox, Outcome, RejectReason, SignalSink};

/// `#[derive(StateMachine)]` — generates the wrapper plumbing (`Live`, `From`, `Fsm`) for a
/// hand-written typestate enum. See the crate-level docs and `jumpstarter-fsm-macros`.
pub use jumpstarter_fsm_macros::StateMachine;

#[cfg(test)]
mod tests {
    use super::*;
    use tokio_stream::StreamExt;

    // A tiny context-less machine to exercise the derive, `apply`, `is_terminal`, `drive`,
    // and the mailbox. `Gate<S> = Handle<(), S>`.
    #[derive(Clone)]
    struct Closed;
    #[derive(Clone)]
    struct Open;
    #[derive(Clone)]
    struct Broken;

    type Gate<S> = Handle<(), S>;

    enum GateSignal {
        Push,
        Smash,
    }

    #[derive(Clone, StateMachine)]
    enum GateState {
        Closed(Gate<Closed>),
        Open(Gate<Open>),
        #[terminal]
        Broken(Gate<Broken>),
    }

    impl Gate<Closed> {
        fn start() -> GateState {
            GateState::Closed(Handle::new((), Closed))
        }
        fn apply(self, signal: GateSignal) -> GateState {
            match signal {
                GateSignal::Push => self.into_state(Open).into(),
                GateSignal::Smash => self.into_state(Broken).into(),
            }
        }
    }
    impl Gate<Open> {
        fn apply(self, signal: GateSignal) -> GateState {
            match signal {
                GateSignal::Push => self.into_state(Closed).into(),
                GateSignal::Smash => self.into_state(Broken).into(),
            }
        }
    }

    #[test]
    fn apply_routes_live_states_and_terminal_absorbs() {
        let m = Gate::<Closed>::start();
        let m = m.apply(GateSignal::Push);
        assert!(matches!(m, GateState::Open(_)));
        let m = m.apply(GateSignal::Smash);
        assert!(matches!(m, GateState::Broken(_)));
        assert!(m.is_terminal());
        // A terminal state ignores every further signal.
        let m = m.apply(GateSignal::Push);
        assert!(matches!(m, GateState::Broken(_)));
    }

    #[tokio::test]
    async fn drive_yields_states_until_terminal() {
        let signals = tokio_stream::iter([GateSignal::Push, GateSignal::Smash, GateSignal::Push]);
        let states = Gate::<Closed>::start().drive(signals);
        tokio::pin!(states);
        let mut seen = Vec::new();
        while let Some(s) = states.next().await {
            seen.push(match s {
                GateState::Closed(_) => "closed",
                GateState::Open(_) => "open",
                GateState::Broken(_) => "broken",
            });
        }
        // initial(closed) + push->open + smash->broken; the trailing push is never consumed.
        assert_eq!(seen, ["closed", "open", "broken"]);
    }

    #[tokio::test]
    async fn signal_sink_lifts_origin_and_envelope_observes_outcome() {
        // A source-specific signal type that lifts into the machine's signal enum.
        enum FromSensor {
            Bump,
        }
        impl From<FromSensor> for GateSignal {
            fn from(_: FromSensor) -> Self {
                GateSignal::Push
            }
        }

        let (tx, mut mb) = Mailbox::<GateSignal>::channel();
        let sink: SignalSink<FromSensor, GateSignal> = SignalSink::new(tx);
        assert!(sink.send(FromSensor::Bump));

        let env = mb.recv().await.expect("envelope");
        assert!(matches!(env.signal, GateSignal::Push));

        // Outcome observation round-trips through the envelope reply.
        let (env, rx) = Envelope::with_reply(GateSignal::Smash);
        ack(env.reply, Outcome::Committed);
        assert!(matches!(rx.await.unwrap(), Outcome::Committed));
    }
}
