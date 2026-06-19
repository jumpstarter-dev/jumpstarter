//! The Jumpstarter `jmp` binary — a thin wrapper over the library dispatch
//! (`jumpstarter_cli::dispatch`), which the language bindings also drive via FFI
//! (`jumpstarter-core-uniffi::run_cli`). The `run` (driver host) and `j` (driver-client)
//! commands stay in each language's entrypoint and reach the core through the foreign-trait
//! seam; everything else is pure Rust dispatched in the library.

use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    jumpstarter_cli::init_tracing();
    ExitCode::from(jumpstarter_cli::dispatch(std::env::args().collect()).await)
}
