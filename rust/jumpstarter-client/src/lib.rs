//! Jumpstarter client runtime (spec doc 04).
//!
//! Currently implements the controller-facing **lease lifecycle** — building an
//! authenticated channel to the controller and acquiring/releasing leases over
//! `jumpstarter.client.v1.ClientService`. The router-dial transport, local-UDS
//! `JUMPSTARTER_HOST` server, and shell orchestration land in subsequent steps
//! (`rust/docs/01-interop-and-migration.md`).

pub mod channel;
pub mod condition;
pub mod dial;
pub mod error;
pub mod exporter_logs;
pub mod insecure;
pub mod lease;
pub mod router;
pub mod selectors;
pub mod service;
pub mod shell;
pub mod transport;

/// A process-wide **multi-threaded** tokio runtime for driving client gRPC connections.
///
/// The FFI extension runs its async exports through uniffi's `async_runtime = "tokio"`, which
/// uses `async-compat`'s **single-threaded** (`new_current_thread`) global runtime. Driving an
/// h2 connection's I/O — framing, flow-control, socket writes — on that one thread caps a bulk
/// resource/flash transfer at a few MiB/s (CPU-bound on the single thread; verified by profiling
/// the flash client at 99% on one core in `h2::Connection::poll`/`framed_write::flush`).
/// Establishing a channel with `.connect()` spawned on this runtime moves that connection's
/// driver task here, so transfers scale across cores instead of serializing on async-compat's
/// thread. (Native `jmp` already runs on its own multi-thread runtime; this is for the embedded
/// in-Python core.)
pub fn io_runtime() -> &'static tokio::runtime::Runtime {
    use std::sync::OnceLock;
    static RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .thread_name("jmp-client-io")
            .build()
            .expect("build jumpstarter client IO runtime")
    })
}

pub use channel::AuthInterceptor;
pub use error::{ClientError, LeaseError};
pub use lease::{acquire, AcquiredLease, CreateLeaseParams, LeaseProvider, LeaseTiming, LeaseView};
pub use selectors::{extract_match_labels_filter, parse_label_selector, selector_contains};
pub use service::ControllerClient;
pub use shell::ShellOptions;
pub use transport::{serve, serve_default, TransportHost};
