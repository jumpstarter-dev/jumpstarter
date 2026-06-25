//! The driver-call error taxonomy that crosses the foreign-host boundary.
//!
//! A foreign (Python/Kotlin/C) host reports failures as one of these variants; the
//! exporter maps each to the same `tonic::Status` the legacy gRPC proxy produced
//! (`driver/base.py:131-138, 369-372`), so remote clients observe identical codes.
//!
//! The mapping `DriverCallError -> tonic::Status` deliberately lives in the exporter
//! (the only crate that speaks tonic at the seam), keeping this facade tonic-free.

/// A driver-call failure reported by the foreign host. Variants mirror the Python
/// exception→status table exactly; the foreign adapter is responsible for catching
/// every host exception and mapping it to one of these (an unmapped error must never
/// escape — UniFFI panics on an undeclared error type).
#[derive(Debug, Clone, thiserror::Error)]
pub enum DriverCallError {
    /// `NotImplementedError` → `UNIMPLEMENTED`.
    #[error("unimplemented: {0}")]
    Unimplemented(String),
    /// `ValueError` → `INVALID_ARGUMENT`.
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    /// `TimeoutError` → `DEADLINE_EXCEEDED`.
    #[error("deadline exceeded: {0}")]
    DeadlineExceeded(String),
    /// Missing method / missing `@export` marker → `NOT_FOUND`.
    #[error("not found: {0}")]
    NotFound(String),
    /// Any other exception → `UNKNOWN`.
    #[error("unknown: {0}")]
    Unknown(String),
}

impl DriverCallError {
    /// A stable string tag for the variant (used by tests and the codec round-trip).
    pub fn code(&self) -> &'static str {
        match self {
            Self::Unimplemented(_) => "Unimplemented",
            Self::InvalidArgument(_) => "InvalidArgument",
            Self::DeadlineExceeded(_) => "DeadlineExceeded",
            Self::NotFound(_) => "NotFound",
            Self::Unknown(_) => "Unknown",
        }
    }

    /// The detail message carried with the error (the Python `str(e)`).
    pub fn message(&self) -> &str {
        match self {
            Self::Unimplemented(m)
            | Self::InvalidArgument(m)
            | Self::DeadlineExceeded(m)
            | Self::NotFound(m)
            | Self::Unknown(m) => m,
        }
    }
}

/// The single mapping of a driver-call error to the `tonic::Status` remote clients observe — the
/// same code+message table the Python `context.abort(...)` produced. Used by every backend that
/// serves a driver (the foreign-host adapter and native Rust drivers).
impl From<DriverCallError> for tonic::Status {
    fn from(e: DriverCallError) -> Self {
        match e {
            DriverCallError::Unimplemented(m) => tonic::Status::unimplemented(m),
            DriverCallError::InvalidArgument(m) => tonic::Status::invalid_argument(m),
            DriverCallError::DeadlineExceeded(m) => tonic::Status::deadline_exceeded(m),
            DriverCallError::NotFound(m) => tonic::Status::not_found(m),
            DriverCallError::Unknown(m) => tonic::Status::unknown(m),
        }
    }
}

/// A controller/lease operation failure (the programmatic lease surface — [`crate::controller`]).
/// Mirrors the meaningful `jumpstarter_client::ClientError`/`LeaseError` cases the Python
/// `Lease` shim and `jumpstarter-testing` need to distinguish.
#[derive(Debug, thiserror::Error)]
pub enum ControllerError {
    /// Missing/invalid connection config (endpoint/token).
    #[error("config error: {0}")]
    Config(String),
    /// Could not connect to or transport to the controller.
    #[error("connection error: {0}")]
    Connection(String),
    /// The selector cannot be satisfied / is invalid (no matching exporter).
    #[error("unsatisfiable: {0}")]
    Unsatisfiable(String),
    /// Lease acquisition timed out.
    #[error("timeout: {0}")]
    Timeout(String),
    /// Any other controller/lease failure.
    #[error("{0}")]
    Other(String),
}

impl From<jumpstarter_client::ClientError> for ControllerError {
    fn from(e: jumpstarter_client::ClientError) -> Self {
        use jumpstarter_client::{ClientError, LeaseError};
        match e {
            ClientError::Config(m) => ControllerError::Config(m),
            ClientError::Transport(t) => ControllerError::Connection(t.to_string()),
            ClientError::Lease(LeaseError::Unsatisfiable(m) | LeaseError::Invalid(m)) => {
                ControllerError::Unsatisfiable(m)
            }
            ClientError::Lease(LeaseError::Timeout { name, timeout_secs }) => {
                ControllerError::Timeout(format!("lease {name} not ready within {timeout_secs}s"))
            }
            other => ControllerError::Other(other.to_string()),
        }
    }
}

