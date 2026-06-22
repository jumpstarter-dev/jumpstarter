//! The per-lease driver-host factory, over the shared transport seam.
//!
//! The `DriverBackend` seam and its channel-backed [`ChannelBackend`] impl live in
//! `jumpstarter-transport` (shared with the client, which consumes them without pulling in
//! this exporter crate). This module re-exports them and adds the exporter-specific
//! [`HostFactory`]: it provisions a fresh driver host per lease and hands back a
//! `DriverBackend` for the lease's router to route into. The concrete factory is
//! [`crate::polyglot::PolyglotHostFactory`] (one subprocess per driver).

use std::sync::Arc;

pub use jumpstarter_transport::{
    ChannelBackend, DriverBackend, FrameUplink, HostGuard, ResponseStream, RouterStreamOpen,
};

/// Produces a fresh driver host for each lease. The exporter is generic over this so the
/// *same* lease loop drives the out-of-process per-driver hosts
/// ([`crate::polyglot::PolyglotHostFactory`]) or, in future, an in-process foreign host.
#[tonic::async_trait]
pub trait HostFactory: Send + Sync + 'static {
    /// Provision a fresh host: a backend to route a lease's calls into, plus a guard held
    /// for the lease lifetime. A fresh tree per lease (fresh drivers) is the contract.
    async fn provision(&self)
        -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), crate::Error>;
}
