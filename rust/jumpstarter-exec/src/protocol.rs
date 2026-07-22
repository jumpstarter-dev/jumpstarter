// NDJSON protocol messages for jumpstarter-exec.
//
// Communication between the exec client and the serve server uses
// newline-delimited JSON (NDJSON) over a Unix stream socket.
// Each message is a single JSON object terminated by a newline.
// Binary data (stdin/stdout/stderr) is base64-encoded.
//
// # Alternative approach considered: SCM_RIGHTS fd-passing
//
// We considered using `SCM_RIGHTS` ancillary data on Unix sockets to pass
// file descriptors (stdin/stdout/stderr pipes) directly from the client to
// the server. This "zero-copy" approach would let the server splice the
// client's actual file descriptors to the child process, avoiding the
// base64 encoding overhead entirely.
//
// However, the Rust standard library API for ancillary data
// (`unix_socket_ancillary_data`) is still unstable (nightly-only) as of
// Rust 1.92. Using it would require either:
// - Building with nightly Rust (not supported by Red Hat's rust-toolset)
// - Adding the `nix` crate (large dependency tree)
//
// When `unix_socket_ancillary_data` stabilizes, we should migrate to
// fd-passing for better performance with binary-heavy workloads.
// Tracking issue: https://github.com/rust-lang/rust/issues/76915

use serde::{Deserialize, Serialize};

/// Messages sent from the exec client to the serve server.
#[derive(Serialize, Deserialize, Debug)]
#[serde(tag = "type")]
pub enum ClientMessage {
    /// Request to execute a command.
    Exec {
        argv: Vec<String>,
        #[serde(default)]
        env: Vec<(String, String)>,
        #[serde(default)]
        cwd: Option<String>,
    },
    /// Stdin data for the running child process (base64-encoded).
    Stdin { data: String },
    /// Close the child's stdin.
    StdinClose,
    /// Send a signal to the child process.
    Signal { signal: i32 },
}

/// Messages sent from the serve server to the exec client.
#[derive(Serialize, Deserialize, Debug)]
#[serde(tag = "type")]
pub enum ServerMessage {
    /// Child process started successfully.
    Started { pid: u32 },
    /// Stdout data from the child process (base64-encoded).
    Stdout { data: String },
    /// Stderr data from the child process (base64-encoded).
    Stderr { data: String },
    /// Child process exited. `code` is None if killed by signal.
    Exit { code: Option<i32> },
    /// An error occurred.
    Error { message: String },
}
