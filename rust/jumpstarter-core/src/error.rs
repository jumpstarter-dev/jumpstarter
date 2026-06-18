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

/// A value-codec failure at the FFI seam (malformed JSON crossing the boundary). These
/// are framework bugs, not driver errors, but are surfaced as `Unknown` to clients.
#[derive(Debug, thiserror::Error)]
pub enum CodecError {
    #[error("encoding driver-call value to/from JSON: {0}")]
    Json(#[from] serde_json::Error),
}

impl From<CodecError> for DriverCallError {
    fn from(e: CodecError) -> Self {
        DriverCallError::Unknown(e.to_string())
    }
}
