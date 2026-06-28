//! Local driver-test harness — the Rust analog of Python's `jumpstarter.common.utils.serve(driver)`
//! and a one-call Java `LocalExporter.serve(driver)`.
//!
//! [`serve`] stands up a driver impl over the **real** transport — a SHM driver host
//! ([`jumpstarter_driver::serve_driver`]) federated through the **production** exporter
//! session ([`jumpstarter_exporter::session::serve_native_host`]) on a private UDS — and connects a
//! [`ClientSession`]. So a driver author exercises their `impl <Interface>` + the generated client
//! through the full `client → exporter → SHM → tonic service` loop (no controller, no lease, no
//! hand-rolled fixture). Build the generated typed client from [`Harness::session`]:
//!
//! ```ignore
//! let h = jumpstarter_driver_harness::serve(
//!     "power", POWER_CLIENT_CLASS, proto::FILE_DESCRIPTOR_SET,
//!     proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()),
//! ).await;
//! let power = PowerClient::new(h.session(), "power").await.unwrap();
//! power.on().await.unwrap();
//! ```

use std::convert::Infallible;
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_client::ClientSession;
use jumpstarter_transport::DriverBackend;

/// A running local exporter plus a connected [`ClientSession`]. Drop it to tear down — the server
/// task is aborted and the temp dir (holding the UDS) removed.
pub struct Harness {
    session: ClientSession,
    server: tokio::task::JoinHandle<()>,
    _dir: tempfile::TempDir,
}

impl Harness {
    /// The connected client session — build the generated typed client from it, e.g.
    /// `PowerClient::new(harness.session(), "power")`.
    pub fn session(&self) -> &ClientSession {
        &self.session
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        self.server.abort();
    }
}

/// Serve a stock `tonic` driver `service` locally and connect a [`ClientSession`]. `name` is the
/// driver-instance name the client resolves; `client_class` and `descriptor` mirror
/// [`jumpstarter_driver::serve_driver`]. Panics on setup failure (it is a test helper).
pub async fn serve<S>(name: &str, client_class: &str, descriptor: &[u8], service: S) -> Harness
where
    S: tower::Service<
            http::Request<tonic::body::BoxBody>,
            Response = http::Response<tonic::body::BoxBody>,
            Error = Infallible,
        > + tonic::server::NamedService
        + Clone
        + Send
        + Sync
        + 'static,
    S::Future: Send + 'static,
{
    let backend = jumpstarter_driver::serve_driver(
        name,
        client_class,
        descriptor.to_vec(),
        service,
    )
    .await
    .expect("serve_driver over SHM");
    serve_backend(backend).await
}

/// Serve an already-built [`DriverBackend`] (e.g. a composite tree) locally and connect a session.
pub async fn serve_backend(backend: Arc<dyn DriverBackend>) -> Harness {
    let dir = tempfile::tempdir().expect("tempdir");
    // Unix socket paths are length-capped (SUN_LEN); a short name under the temp dir stays safe.
    let uds = dir.path().join("e.sock");
    let uds_path = uds.clone();
    let server = tokio::spawn(async move {
        // The PRODUCTION exporter session serving one backend — the same `serve_native_host` the hub
        // and the standalone hosts use, so the test drives the real dispatch stack, not a fixture.
        let _ = jumpstarter_exporter::session::serve_native_host(&uds_path, backend).await;
    });

    // `serve_native_host` binds the socket on a spawned task; wait for it before connecting.
    for _ in 0..500 {
        if uds.exists() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let session = ClientSession::connect(uds.to_string_lossy().into_owned())
        .await
        .expect("connect ClientSession over the local exporter UDS");

    Harness {
        session,
        server,
        _dir: dir,
    }
}
