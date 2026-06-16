//! Client error taxonomy (spec doc 04 §error taxonomy).

use thiserror::Error;

/// Errors from the client runtime.
///
/// The `tonic` error types are large, so they are boxed to keep `ClientError`
/// (and every `Result` returning it) small.
#[derive(Debug, Error)]
pub enum ClientError {
    /// A controller RPC failed at the transport/status level.
    #[error("controller rpc error: {0}")]
    Rpc(Box<tonic::Status>),
    /// Connecting/building the controller channel failed.
    #[error("transport error: {0}")]
    Transport(Box<tonic::transport::Error>),
    /// Configuration was missing or invalid (e.g. no endpoint/token).
    #[error("configuration error: {0}")]
    Config(String),
    /// A lease could not be acquired or was not satisfiable.
    #[error("lease error: {0}")]
    Lease(#[from] LeaseError),
}

impl ClientError {
    /// Whether this is a transient transport error worth retrying — a gRPC
    /// `UNAVAILABLE` status (Python maps this to `ConnectionError`, retried by
    /// `_get_with_retry`).
    pub fn is_transient(&self) -> bool {
        matches!(self, ClientError::Rpc(status) if status.code() == tonic::Code::Unavailable)
    }
}

impl From<tonic::Status> for ClientError {
    fn from(status: tonic::Status) -> Self {
        ClientError::Rpc(Box::new(status))
    }
}

impl From<tonic::transport::Error> for ClientError {
    fn from(err: tonic::transport::Error) -> Self {
        ClientError::Transport(Box::new(err))
    }
}

/// Lease lifecycle failures (`client/lease.py` raises `LeaseError`).
#[derive(Debug, Error, PartialEq, Eq)]
pub enum LeaseError {
    /// The lease cannot be satisfied (`Unsatisfiable`, non-`NoExporter`).
    #[error("the lease cannot be satisfied: {0}")]
    Unsatisfiable(String),
    /// The lease is invalid (`Invalid`).
    #[error("the lease is invalid: {0}")]
    Invalid(String),
    /// The lease left `Pending` without reaching `Ready`/`Unsatisfiable`/`Invalid`.
    #[error("lease {0} is not in pending, but it isn't in Ready or Unsatisfiable state either")]
    NotPending(String),
    /// The lease was released (`Ready=False`, reason `Released`).
    #[error("lease {0} released")]
    Released(String),
    /// Acquisition exceeded `acquisition_timeout`.
    #[error("lease {name} acquisition timed out after {timeout_secs} seconds")]
    Timeout { name: String, timeout_secs: u64 },
    /// A pre-existing lease (env/flag) is owned by another client.
    #[error("lease {name} belongs to client '{owner}', not the current client '{current}'")]
    WrongOwner {
        name: String,
        owner: String,
        current: String,
    },
}
