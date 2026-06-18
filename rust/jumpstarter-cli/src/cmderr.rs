//! Command error type carrying the intended exit code (spec 08 §5.2): usage
//! errors map to exit 2 (click `UsageError`/`BadParameter`), everything else to
//! exit 1 (click `ClickException`). Both print `Error: <message>` to stderr.

use std::process::ExitCode;

#[derive(Debug)]
pub enum CmdError {
    /// A usage error (exit 2).
    Usage(String),
    /// A runtime/click error (exit 1).
    Runtime(String),
}

impl CmdError {
    /// Print to stderr and convert to the process exit code.
    pub fn report(self) -> ExitCode {
        match self {
            CmdError::Usage(m) => {
                eprintln!("Error: {m}");
                ExitCode::from(2)
            }
            CmdError::Runtime(m) => {
                eprintln!("Error: {m}");
                ExitCode::from(1)
            }
        }
    }
}

impl std::fmt::Display for CmdError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CmdError::Usage(m) | CmdError::Runtime(m) => write!(f, "{m}"),
        }
    }
}

/// Map any displayable error into a runtime [`CmdError`].
pub fn runtime<E: std::fmt::Display>(e: E) -> CmdError {
    CmdError::Runtime(e.to_string())
}

/// Map a controller [`ClientError`](jumpstarter_client::ClientError) into a
/// runtime error using its Python-compatible gRPC message.
pub fn grpc(e: jumpstarter_client::ClientError) -> CmdError {
    CmdError::Runtime(e.user_message())
}

impl From<String> for CmdError {
    fn from(message: String) -> Self {
        CmdError::Runtime(message)
    }
}
