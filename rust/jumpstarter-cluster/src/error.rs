//! Cluster provisioning errors — a flat enum covering the Python exception
//! hierarchy (`jumpstarter_kubernetes/exceptions.py` + the cluster modules). At
//! the CLI boundary these map to `CmdError::Runtime` so the message matches the
//! Python `click.ClickException(str(e))`.

#[derive(Debug, thiserror::Error)]
pub enum ClusterError {
    #[error("Command list cannot be empty")]
    EmptyCommand,

    /// A binary could not be spawned (`FileNotFoundError`/`PermissionError`/`OSError`).
    #[error("Command failed ({program}): {source}")]
    Spawn {
        program: String,
        #[source]
        source: std::io::Error,
    },

    #[error("{tool} is not installed (or not in your PATH){info}")]
    ToolNotInstalled { tool: String, info: String },

    #[error("{0}")]
    Validation(String),

    #[error("{0}")]
    NotFound(String),

    #[error("{0}")]
    AlreadyExists(String),

    /// A cluster operation failed (create/delete/recreate), carrying the rendered cause.
    #[error("{0}")]
    Operation(String),

    #[error("{0}")]
    Certificate(String),

    #[error("{0}")]
    Kubeconfig(String),

    #[error("{0}")]
    Endpoint(String),

    #[error("{0}")]
    Version(String),

    /// The user declined a confirmation prompt.
    #[error("Operation cancelled")]
    Cancelled,
}

impl ClusterError {
    pub fn tool_not_installed(tool: impl Into<String>) -> Self {
        Self::ToolNotInstalled { tool: tool.into(), info: String::new() }
    }
}

pub type Result<T> = std::result::Result<T, ClusterError>;
