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

pub use channel::AuthInterceptor;
pub use error::{ClientError, LeaseError};
pub use lease::{acquire, AcquiredLease, CreateLeaseParams, LeaseProvider, LeaseTiming, LeaseView};
pub use selectors::{extract_match_labels_filter, parse_label_selector, selector_contains};
pub use service::ControllerClient;
pub use shell::ShellOptions;
pub use transport::{serve, serve_default, TransportHost};
