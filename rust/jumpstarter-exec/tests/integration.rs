use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::Duration;

use base64::{engine::general_purpose::STANDARD, Engine as _};
use jumpstarter_exec::protocol::{ClientMessage, ServerMessage};
use jumpstarter_exec::server;
use tempfile::TempDir;

/// Find the compiled test binary in the target directory.
fn binary_path() -> PathBuf {
    let mut path = std::env::current_exe().unwrap();
    // test binary lives in target/debug/deps/; the main binary is in target/debug/
    path.pop();
    if path.ends_with("deps") {
        path.pop();
    }
    path.push("jumpstarter-exec");
    path
}

/// Start `jumpstarter-exec serve` as a real subprocess, returning the
/// child handle, the temp dir (whose lifetime keeps the socket alive),
/// and the socket path.
fn start_server_process() -> (Child, TempDir, String) {
    let dir = TempDir::new().unwrap();
    let sock = dir.path().join("e2e.sock");
    let path = sock.to_str().unwrap().to_string();

    let child = Command::new(binary_path())
        .args(["serve", "--socket", &path])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn jumpstarter-exec serve");

    // Wait for the socket to appear.
    for _ in 0..50 {
        if sock.exists() {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert!(sock.exists(), "server socket never appeared");

    (child, dir, path)
}

/// Run `jumpstarter-exec exec` as a subprocess and return (stdout, stderr, exit code).
fn run_exec(socket: &str, cmd: &[&str], stdin_data: Option<&[u8]>) -> (String, String, i32) {
    let mut args = vec!["exec", "--socket", socket, "--"];
    args.extend_from_slice(cmd);

    let mut child = Command::new(binary_path())
        .args(&args)
        .stdin(if stdin_data.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn jumpstarter-exec exec");

    if let Some(data) = stdin_data {
        let mut child_stdin = child.stdin.take().unwrap();
        child_stdin.write_all(data).ok();
        drop(child_stdin);
    }

    let output = child.wait_with_output().expect("exec wait failed");
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let code = output.status.code().unwrap_or(1);
    (stdout, stderr, code)
}

// ---------- In-process server tests (NDJSON protocol level) ----------

fn start_server() -> (TempDir, String) {
    let dir = TempDir::new().unwrap();
    let sock = dir.path().join("test.sock");
    let path = sock.to_str().unwrap().to_string();
    let p = path.clone();
    thread::spawn(move || {
        server::serve(&p).ok();
    });
    thread::sleep(Duration::from_millis(100));
    (dir, path)
}

fn send(stream: &mut UnixStream, msg: &ClientMessage) {
    let mut buf = serde_json::to_vec(msg).unwrap();
    buf.push(b'\n');
    stream.write_all(&buf).unwrap();
}

fn read_messages(reader: &mut impl BufRead) -> Vec<ServerMessage> {
    let mut msgs = Vec::new();
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {
                let msg: ServerMessage = serde_json::from_str(line.trim()).unwrap();
                let is_terminal = matches!(
                    msg,
                    ServerMessage::Exit { .. } | ServerMessage::Error { .. }
                );
                msgs.push(msg);
                if is_terminal {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    msgs
}

fn collect_stdout(msgs: &[ServerMessage]) -> String {
    let mut out = Vec::new();
    for msg in msgs {
        if let ServerMessage::Stdout { data } = msg {
            out.extend(STANDARD.decode(data).unwrap());
        }
    }
    String::from_utf8(out).unwrap()
}

fn collect_stderr(msgs: &[ServerMessage]) -> String {
    let mut out = Vec::new();
    for msg in msgs {
        if let ServerMessage::Stderr { data } = msg {
            out.extend(STANDARD.decode(data).unwrap());
        }
    }
    String::from_utf8(out).unwrap()
}

fn find_exit_code(msgs: &[ServerMessage]) -> Option<i32> {
    for msg in msgs {
        if let ServerMessage::Exit { code } = msg {
            return *code;
        }
    }
    None
}

#[test]
fn echo_stdout_and_exit_code() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["echo".into(), "hello world".into()],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    assert!(matches!(msgs.first(), Some(ServerMessage::Started { .. })));
    assert_eq!(collect_stdout(&msgs).trim(), "hello world");
    assert_eq!(find_exit_code(&msgs), Some(0));
}

#[test]
fn stderr_forwarding() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["sh".into(), "-c".into(), "echo err >&2".into()],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    assert_eq!(collect_stderr(&msgs).trim(), "err");
    assert_eq!(find_exit_code(&msgs), Some(0));
}

#[test]
fn nonzero_exit_code() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["sh".into(), "-c".into(), "exit 42".into()],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    assert_eq!(find_exit_code(&msgs), Some(42));
}

#[test]
fn env_vars_passed_to_child() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["sh".into(), "-c".into(), "echo $MY_VAR".into()],
            env: vec![("MY_VAR".into(), "test_value".into())],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    assert_eq!(collect_stdout(&msgs).trim(), "test_value");
}

#[test]
fn cwd_passed_to_child() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["pwd".into()],
            env: vec![],
            cwd: Some("/tmp".into()),
        },
    );

    let msgs = read_messages(&mut reader);
    assert_eq!(collect_stdout(&msgs).trim(), "/tmp");
}

#[test]
fn stdin_forwarding() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["cat".into()],
            env: vec![],
            cwd: None,
        },
    );

    // Wait for Started before feeding stdin.
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let started: ServerMessage = serde_json::from_str(line.trim()).unwrap();
    assert!(matches!(started, ServerMessage::Started { .. }));

    let payload = STANDARD.encode(b"from stdin\n");
    send(&mut writer, &ClientMessage::Stdin { data: payload });
    send(&mut writer, &ClientMessage::StdinClose);

    let msgs = read_messages(&mut reader);
    assert_eq!(collect_stdout(&msgs).trim(), "from stdin");
    assert_eq!(find_exit_code(&msgs), Some(0));
}

#[test]
fn signal_kills_child() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["sleep".into(), "60".into()],
            env: vec![],
            cwd: None,
        },
    );

    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let started: ServerMessage = serde_json::from_str(line.trim()).unwrap();
    assert!(matches!(started, ServerMessage::Started { .. }));

    // SIGTERM (15)
    send(&mut writer, &ClientMessage::Signal { signal: 15 });

    let msgs = read_messages(&mut reader);
    let exit = msgs
        .iter()
        .find(|m| matches!(m, ServerMessage::Exit { .. }));
    assert!(exit.is_some(), "expected Exit message after signal");
}

#[test]
fn spawn_nonexistent_command() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec!["/no/such/binary".into()],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    let has_error = msgs
        .iter()
        .any(|m| matches!(m, ServerMessage::Error { .. }));
    assert!(has_error, "expected Error for nonexistent binary");
}

#[test]
fn empty_argv_rejected() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec![],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    let has_error = msgs
        .iter()
        .any(|m| matches!(m, ServerMessage::Error { message } if message.contains("empty argv")));
    assert!(has_error, "expected Error for empty argv");
}

#[test]
fn concurrent_connections() {
    let (_dir, path) = start_server();
    let mut handles = Vec::new();

    for i in 0..5 {
        let p = path.clone();
        handles.push(thread::spawn(move || {
            let stream = UnixStream::connect(&p).unwrap();
            let mut writer = stream.try_clone().unwrap();
            let mut reader = BufReader::new(stream);

            send(
                &mut writer,
                &ClientMessage::Exec {
                    argv: vec!["echo".into(), format!("conn-{i}")],
                    env: vec![],
                    cwd: None,
                },
            );

            let msgs = read_messages(&mut reader);
            let stdout = collect_stdout(&msgs);
            assert_eq!(stdout.trim(), format!("conn-{i}"));
            assert_eq!(find_exit_code(&msgs), Some(0));
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
}

#[test]
fn large_output() {
    let (_dir, path) = start_server();
    let stream = UnixStream::connect(&path).unwrap();
    let mut writer = stream.try_clone().unwrap();
    let mut reader = BufReader::new(stream);

    // Generate 100KB of output.
    send(
        &mut writer,
        &ClientMessage::Exec {
            argv: vec![
                "sh".into(),
                "-c".into(),
                "dd if=/dev/zero bs=1024 count=100 2>/dev/null | base64".into(),
            ],
            env: vec![],
            cwd: None,
        },
    );

    let msgs = read_messages(&mut reader);
    let stdout = collect_stdout(&msgs);
    assert!(
        stdout.len() > 100_000,
        "expected at least 100KB of output, got {}",
        stdout.len()
    );
    assert_eq!(find_exit_code(&msgs), Some(0));
}

// ---------- End-to-end binary tests (serve process + exec process) ----------

#[test]
fn e2e_echo() {
    let (mut server, _dir, sock) = start_server_process();
    let (stdout, _, code) = run_exec(&sock, &["echo", "e2e works"], None);
    assert_eq!(code, 0);
    assert_eq!(stdout.trim(), "e2e works");
    server.kill().ok();
}

#[test]
fn e2e_stderr() {
    let (mut server, _dir, sock) = start_server_process();
    let (_, stderr, code) = run_exec(&sock, &["sh", "-c", "echo oops >&2"], None);
    assert_eq!(code, 0);
    assert!(
        stderr.contains("oops"),
        "stderr should contain 'oops', got: {stderr}"
    );
    server.kill().ok();
}

#[test]
fn e2e_exit_code_propagation() {
    let (mut server, _dir, sock) = start_server_process();
    let (_, _, code) = run_exec(&sock, &["sh", "-c", "exit 7"], None);
    assert_eq!(code, 7);
    server.kill().ok();
}

#[test]
fn e2e_stdin_pipe() {
    let (mut server, _dir, sock) = start_server_process();
    let (stdout, _, code) = run_exec(&sock, &["cat"], Some(b"piped input\n"));
    assert_eq!(code, 0);
    assert_eq!(stdout.trim(), "piped input");
    server.kill().ok();
}

#[test]
fn e2e_large_output() {
    let (mut server, _dir, sock) = start_server_process();
    let (stdout, _, code) = run_exec(
        &sock,
        &[
            "sh",
            "-c",
            "dd if=/dev/zero bs=1024 count=100 2>/dev/null | base64",
        ],
        None,
    );
    assert_eq!(code, 0);
    assert!(
        stdout.len() > 100_000,
        "expected >100KB, got {} bytes",
        stdout.len()
    );
    server.kill().ok();
}

#[test]
fn e2e_concurrent_exec() {
    let (mut server, _dir, sock) = start_server_process();
    let mut handles = Vec::new();

    for i in 0..5 {
        let s = sock.clone();
        handles.push(thread::spawn(move || {
            let msg = format!("worker-{i}");
            let (stdout, _, code) = run_exec(&s, &["echo", &msg], None);
            assert_eq!(code, 0);
            assert_eq!(stdout.trim(), msg);
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
    server.kill().ok();
}

#[test]
fn e2e_nonexistent_command() {
    let (mut server, _dir, sock) = start_server_process();
    let (_, stderr, code) = run_exec(&sock, &["/no/such/binary"], None);
    assert_ne!(code, 0);
    assert!(
        stderr.contains("server error") || stderr.contains("spawn failed"),
        "stderr should mention error, got: {stderr}"
    );
    server.kill().ok();
}

#[test]
fn e2e_version_subcommand() {
    let output = Command::new(binary_path())
        .arg("version")
        .output()
        .expect("failed to run jumpstarter-exec version");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.starts_with("jumpstarter-exec "),
        "unexpected version output: {stdout}"
    );
}

#[test]
fn e2e_debug_json_logs_commands_and_io() {
    let dir = TempDir::new().unwrap();
    let sock = dir.path().join("debug.sock");
    let path = sock.to_str().unwrap().to_string();

    let mut server = Command::new(binary_path())
        .args([
            "serve",
            "--socket",
            &path,
            "--debug",
            "--log-format",
            "json",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn jumpstarter-exec serve --debug");

    for _ in 0..50 {
        if sock.exists() {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert!(sock.exists(), "server socket never appeared");

    let (stdout, _, code) = run_exec(&path, &["echo", "hello-debug"], None);
    assert_eq!(code, 0);
    assert_eq!(stdout.trim(), "hello-debug");

    server.kill().ok();
    let stderr = {
        let mut s = String::new();
        if let Some(mut err) = server.stderr.take() {
            use std::io::Read;
            err.read_to_string(&mut s).ok();
        }
        s
    };

    // Listening line is always emitted (info).
    assert!(
        stderr.lines().any(|l| {
            l.contains(r#""msg":"listening""#) && l.contains(r#""component":"jumpstarter-exec""#)
        }),
        "expected JSON listening log, got:\n{stderr}"
    );

    // Debug mode logs argv and I/O previews.
    assert!(
        stderr.lines().any(|l| {
            l.contains(r#""msg":"exec started""#) && l.contains("echo") && l.contains("hello-debug")
        }),
        "expected exec started with argv, got:\n{stderr}"
    );
    assert!(
        stderr
            .lines()
            .any(|l| l.contains(r#""msg":"stdout""#) && l.contains("hello-debug")),
        "expected stdout preview log, got:\n{stderr}"
    );
    assert!(
        stderr
            .lines()
            .any(|l| l.contains(r#""msg":"exec finished""#)),
        "expected exec finished log, got:\n{stderr}"
    );
}

#[test]
fn e2e_log_fields_appear_on_every_line() {
    let dir = TempDir::new().unwrap();
    let sock = dir.path().join("fields.sock");
    let path = sock.to_str().unwrap().to_string();

    let mut server = Command::new(binary_path())
        .args([
            "serve",
            "--socket",
            &path,
            "--log-field",
            "exporter=demo-abc",
            "--log-field",
            "namespace=jumpstarter-lab",
            "--log-field",
            "component=exporter",
        ])
        .env_remove("JUMPSTARTER_EXEC_LOG_FIELDS")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn serve with log fields");

    for _ in 0..50 {
        if sock.exists() {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert!(sock.exists(), "server socket never appeared");

    let (_, _, code) = run_exec(&path, &["true"], None);
    assert_eq!(code, 0);

    server.kill().ok();
    let stderr = {
        let mut s = String::new();
        if let Some(mut err) = server.stderr.take() {
            use std::io::Read;
            err.read_to_string(&mut s).ok();
        }
        s
    };

    let json_lines: Vec<&str> = stderr.lines().filter(|l| l.starts_with('{')).collect();
    assert!(
        !json_lines.is_empty(),
        "expected JSON log lines, got:\n{stderr}"
    );
    for line in &json_lines {
        assert!(
            line.contains(r#""exporter":"demo-abc""#),
            "missing exporter field in: {line}"
        );
        assert!(
            line.contains(r#""namespace":"jumpstarter-lab""#),
            "missing namespace field in: {line}"
        );
        assert!(
            line.contains(r#""component":"exporter""#),
            "missing component=exporter in: {line}"
        );
    }
}
