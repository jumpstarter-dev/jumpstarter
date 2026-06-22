//! The per-lease driver-host factory, over the shared transport seam.
//!
//! The `DriverBackend` seam and its channel-backed [`ChannelBackend`] impl live in
//! `jumpstarter-transport` (shared with the client, which consumes them without pulling in
//! this exporter crate). This module re-exports them and adds the exporter-specific
//! [`HostFactory`]: it provisions a fresh driver host per lease (spawning the slim
//! subprocess) and hands back a `DriverBackend` for the lease's router to route into.

use std::path::PathBuf;
use std::sync::Arc;

pub use jumpstarter_transport::{
    ChannelBackend, DriverBackend, FrameUplink, HostGuard, ResponseStream, RouterStreamOpen,
};

/// Produces a fresh driver host for each lease. The exporter is generic over this so the
/// *same* lease loop drives either the out-of-process slim host ([`SlimHostFactory`]) or
/// an in-process foreign host (jumpstarter-core's foreign factory).
#[tonic::async_trait]
pub trait HostFactory: Send + Sync + 'static {
    /// Provision a fresh host: a backend to route a lease's calls into, plus a guard held
    /// for the lease lifetime. A fresh tree per lease (fresh drivers) is the contract.
    async fn provision(&self)
        -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), crate::Error>;
}

/// The legacy factory: spawn a [`crate::driver_host::SlimHost`] subprocess and route into
/// it over its private UDS channel. The `SlimHost` is the lease guard (drop = SIGKILL).
pub struct SlimHostFactory {
    config_path: PathBuf,
}

impl SlimHostFactory {
    pub fn new(config_path: PathBuf) -> Self {
        Self { config_path }
    }
}

#[tonic::async_trait]
impl HostFactory for SlimHostFactory {
    async fn provision(
        &self,
    ) -> Result<(Arc<dyn DriverBackend>, Box<dyn HostGuard>), crate::Error> {
        let host = crate::driver_host::SlimHost::spawn(&self.config_path).await?;
        let channel = crate::control::uds_channel(host.socket()).await?;
        let backend: Arc<dyn DriverBackend> = Arc::new(ChannelBackend::new(channel));
        Ok((backend, Box::new(host)))
    }
}
