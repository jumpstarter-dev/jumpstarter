//! Admin error taxonomy.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum AdminError {
    #[error("kubernetes error: {0}")]
    Kube(#[from] kube::Error),
    #[error("kubeconfig error: {0}")]
    Config(String),
    #[error("{0}")]
    Other(String),
}
