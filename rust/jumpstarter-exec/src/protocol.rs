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
#[derive(Serialize, Deserialize, Debug, PartialEq)]
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_exec_roundtrip() {
        let msg = ClientMessage::Exec {
            argv: vec!["echo".into(), "hello".into()],
            env: vec![("FOO".into(), "bar".into())],
            cwd: Some("/tmp".into()),
        };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ClientMessage = serde_json::from_str(&json).unwrap();
        match parsed {
            ClientMessage::Exec { argv, env, cwd } => {
                assert_eq!(argv, vec!["echo", "hello"]);
                assert_eq!(env, vec![("FOO".to_string(), "bar".to_string())]);
                assert_eq!(cwd, Some("/tmp".into()));
            }
            _ => panic!("expected Exec"),
        }
    }

    #[test]
    fn client_exec_defaults() {
        let json = r#"{"type":"Exec","argv":["ls"]}"#;
        let msg: ClientMessage = serde_json::from_str(json).unwrap();
        match msg {
            ClientMessage::Exec { argv, env, cwd } => {
                assert_eq!(argv, vec!["ls"]);
                assert!(env.is_empty());
                assert!(cwd.is_none());
            }
            _ => panic!("expected Exec"),
        }
    }

    #[test]
    fn client_stdin_roundtrip() {
        let msg = ClientMessage::Stdin {
            data: "aGVsbG8=".into(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ClientMessage = serde_json::from_str(&json).unwrap();
        match parsed {
            ClientMessage::Stdin { data } => assert_eq!(data, "aGVsbG8="),
            _ => panic!("expected Stdin"),
        }
    }

    #[test]
    fn client_stdin_close_roundtrip() {
        let json = serde_json::to_string(&ClientMessage::StdinClose).unwrap();
        assert_eq!(json, r#"{"type":"StdinClose"}"#);
    }

    #[test]
    fn client_signal_roundtrip() {
        let msg = ClientMessage::Signal { signal: 15 };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains("\"signal\":15"));
    }

    #[test]
    fn server_started_roundtrip() {
        let msg = ServerMessage::Started { pid: 42 };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ServerMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, ServerMessage::Started { pid: 42 });
    }

    #[test]
    fn server_exit_with_code() {
        let msg = ServerMessage::Exit { code: Some(0) };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ServerMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, ServerMessage::Exit { code: Some(0) });
    }

    #[test]
    fn server_exit_signal_killed() {
        let msg = ServerMessage::Exit { code: None };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ServerMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed, ServerMessage::Exit { code: None });
    }

    #[test]
    fn server_error_roundtrip() {
        let msg = ServerMessage::Error {
            message: "boom".into(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        let parsed: ServerMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(
            parsed,
            ServerMessage::Error {
                message: "boom".into()
            }
        );
    }

    #[test]
    fn tagged_discriminator() {
        let json = r#"{"type":"Stdout","data":"AAAA"}"#;
        let msg: ServerMessage = serde_json::from_str(json).unwrap();
        assert_eq!(
            msg,
            ServerMessage::Stdout {
                data: "AAAA".into()
            }
        );
    }
}
