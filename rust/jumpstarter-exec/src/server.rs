use std::collections::HashSet;
use std::io::{BufRead, BufReader, ErrorKind, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};

use crate::log::{io_preview, LogFormat, Logger};
use crate::protocol::{ClientMessage, ServerMessage};

extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
    fn umask(mask: u32) -> u32;
}

/// Options for `serve`.
#[derive(Clone, Debug)]
pub struct ServeOptions {
    pub debug: bool,
    pub log_format: LogFormat,
    /// Persistent JEP-0013 correlation fields (exporter, namespace, …).
    pub log_fields: std::collections::BTreeMap<String, String>,
}

impl Default for ServeOptions {
    fn default() -> Self {
        Self {
            debug: false,
            // JSON by default so container logs align with JEP-0013 /
            // controller zap / exporter structlog output.
            log_format: LogFormat::Json,
            log_fields: std::collections::BTreeMap::new(),
        }
    }
}

/// Shared lifecycle for Shutdown vs Exec: shutdown flag and in-flight
/// child PIDs share one mutex so a concurrent Exec cannot register a
/// child after Shutdown has snapshotted (and orphan it without SIGTERM).
struct Lifecycle {
    shutdown: bool,
    children: HashSet<u32>,
}

struct RuntimeState {
    lifecycle: Mutex<Lifecycle>,
}

impl RuntimeState {
    fn new() -> Self {
        Self {
            lifecycle: Mutex::new(Lifecycle {
                shutdown: false,
                children: HashSet::new(),
            }),
        }
    }

    fn is_shutdown(&self) -> bool {
        self.lifecycle.lock().unwrap().shutdown
    }

    /// Mark shutdown and return PIDs that need SIGTERM.
    fn begin_shutdown(&self) -> Vec<u32> {
        let mut life = self.lifecycle.lock().unwrap();
        life.shutdown = true;
        life.children.iter().copied().collect()
    }

    /// Register a newly spawned Exec child, or reject it if shutdown already
    /// started (caller must terminate/reap the child).
    fn register_child(&self, pid: u32) -> bool {
        let mut life = self.lifecycle.lock().unwrap();
        if life.shutdown {
            return false;
        }
        life.children.insert(pid);
        true
    }

    fn unregister_child(&self, pid: u32) {
        self.lifecycle.lock().unwrap().children.remove(&pid);
    }

    fn child_pids(&self) -> Vec<u32> {
        self.lifecycle
            .lock()
            .unwrap()
            .children
            .iter()
            .copied()
            .collect()
    }
}

/// Listen on `socket_path` with default options (JSON logs, debug off).
pub fn serve(socket_path: &str) -> std::io::Result<()> {
    serve_with(socket_path, ServeOptions::default())
}

/// Listen on `socket_path` with the given options.
pub fn serve_with(socket_path: &str, opts: ServeOptions) -> std::io::Result<()> {
    let log = Arc::new(Logger::new(opts.log_format, opts.debug, opts.log_fields));
    let state = Arc::new(RuntimeState::new());

    // Shared-volume sidecar pattern: the exporter often runs as a different
    // UID than this process (e.g. runtime root + exporter 65532).
    // Mask execute bits during bind() so the listen socket is born 0o666
    // (cross-UID accessible, not executable). Then clear umask so Exec
    // children (QEMU) create QMP/serial/VNC sockets with mode 0o777.
    #[cfg(unix)]
    unsafe {
        umask(0o111);
    }

    if std::path::Path::new(socket_path).exists() {
        if UnixStream::connect(socket_path).is_ok() {
            return Err(std::io::Error::other(format!(
                "another server is already listening on {socket_path}"
            )));
        }
        std::fs::remove_file(socket_path)?;
    }
    let listener = UnixListener::bind(socket_path)?;
    listener.set_nonblocking(true)?;

    #[cfg(unix)]
    unsafe {
        umask(0);
    }
    log.info(
        "listening",
        &[("socket", json!(socket_path)), ("debug", json!(opts.debug))],
    );
    // Ensure listening log is flushed so tests that poll the socket get
    // visible evidence that the server is ready.
    let _ = std::io::stderr().flush();

    while !state.is_shutdown() {
        match listener.accept() {
            Ok((s, _)) => {
                let log = Arc::clone(&log);
                let state = Arc::clone(&state);
                let socket = socket_path.to_string();
                thread::spawn(move || {
                    if let Err(e) = handle_connection(s, Arc::clone(&log), &socket, state) {
                        log.error("connection error", &[("error", json!(e.to_string()))]);
                    }
                });
            }
            Err(e) if e.kind() == ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(50));
            }
            Err(e) => log.error("accept error", &[("error", json!(e.to_string()))]),
        }
    }

    // Best-effort: signal any remaining Exec children and give their
    // handler threads a moment to wait()/reap before we return.
    let leftover = state.child_pids();
    for &pid in &leftover {
        unsafe {
            kill(pid as i32, 15);
        }
    }
    if !leftover.is_empty() {
        thread::sleep(Duration::from_millis(200));
    }

    let _ = std::fs::remove_file(socket_path);
    log.info("shutdown complete", &[]);
    Ok(())
}

fn handle_connection(
    stream: UnixStream,
    log: Arc<Logger>,
    socket_path: &str,
    state: Arc<RuntimeState>,
) -> std::io::Result<()> {
    let reader = BufReader::new(stream.try_clone()?);
    let writer: Arc<Mutex<UnixStream>> = Arc::new(Mutex::new(stream));
    let mut lines = reader.lines();

    let first_line = lines
        .next()
        .ok_or_else(|| io_err("client disconnected before sending a request"))??;

    let msg: ClientMessage =
        serde_json::from_str(&first_line).map_err(|e| io_err(&format!("invalid message: {e}")))?;

    match msg {
        ClientMessage::Shutdown => {
            log.info("shutdown requested", &[("socket", json!(socket_path))]);
            // Acknowledge before stopping the accept loop so the client
            // observes success. Do not process::exit — that skips Drop
            // and can leave Exec children as zombies when we are PID 1.
            send(&writer, &ServerMessage::Exit { code: Some(0) })?;
            drop(writer);
            let _ = std::fs::remove_file(socket_path);
            // Mark shutdown under the same lock used for child registration,
            // then SIGTERM the snapshotted PIDs.
            let pids = state.begin_shutdown();
            for pid in pids {
                unsafe {
                    kill(pid as i32, 15);
                }
            }
            Ok(())
        }
        ClientMessage::Exec { argv, env, cwd } => {
            handle_exec(argv, env, cwd, lines, writer, log, state)
        }
        _ => Err(io_err("first message must be Exec or Shutdown")),
    }
}

fn terminate_child(mut child: Child) {
    let pid = child.id();
    unsafe {
        kill(pid as i32, 15);
    }
    let _ = child.wait();
}

fn handle_exec(
    argv: Vec<String>,
    env: Vec<(String, String)>,
    cwd: Option<String>,
    lines: std::io::Lines<BufReader<UnixStream>>,
    writer: Arc<Mutex<UnixStream>>,
    log: Arc<Logger>,
    state: Arc<RuntimeState>,
) -> std::io::Result<()> {
    if argv.is_empty() {
        send(
            &writer,
            &ServerMessage::Error {
                message: "empty argv".into(),
            },
        )?;
        return Err(io_err("empty argv"));
    }

    log.debug(
        "exec request",
        &[
            ("argv", json!(argv)),
            ("cwd", json!(cwd)),
            ("env_count", json!(env.len())),
        ],
    );

    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    for (k, v) in &env {
        cmd.env(k, v);
    }
    if let Some(ref dir) = cwd {
        cmd.current_dir(dir);
    }

    let mut child = cmd.spawn().map_err(|e| {
        log.error(
            "spawn failed",
            &[("argv", json!(argv)), ("error", json!(e.to_string()))],
        );
        let msg = format!("spawn failed: {e}");
        let _ = send(
            &writer,
            &ServerMessage::Error {
                message: msg.clone(),
            },
        );
        io_err(&msg)
    })?;

    let pid = child.id();
    if !state.register_child(pid) {
        // Shutdown won the race: do not leave an untracked child running.
        log.info(
            "exec rejected: server shutting down",
            &[("pid", json!(pid)), ("argv", json!(argv))],
        );
        terminate_child(child);
        let msg = "server is shutting down".to_string();
        let _ = send(
            &writer,
            &ServerMessage::Error {
                message: msg.clone(),
            },
        );
        return Err(io_err(&msg));
    }
    log.info(
        "exec started",
        &[("pid", json!(pid)), ("argv", json!(argv))],
    );
    send(&writer, &ServerMessage::Started { pid })?;

    let child_stdin = child.stdin.take();
    let child_stdout = child.stdout.take().unwrap();
    let child_stderr = child.stderr.take().unwrap();

    let w = Arc::clone(&writer);
    let log_out = Arc::clone(&log);
    let stdout_handle = thread::spawn(move || {
        forward_output(child_stdout, &w, false, log_out.as_ref(), pid);
    });

    let w = Arc::clone(&writer);
    let log_err = Arc::clone(&log);
    let stderr_handle = thread::spawn(move || {
        forward_output(child_stderr, &w, true, log_err.as_ref(), pid);
    });

    let child_stdin = Arc::new(Mutex::new(child_stdin));
    let stdin_ref = Arc::clone(&child_stdin);
    let log_in = Arc::clone(&log);
    let reaped = Arc::new(AtomicBool::new(false));
    let reaped_ref = Arc::clone(&reaped);
    let _reader_handle = thread::spawn(move || {
        for line in lines {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            let msg: ClientMessage = match serde_json::from_str(&line) {
                Ok(m) => m,
                Err(_) => continue,
            };
            match msg {
                ClientMessage::Stdin { data } => {
                    if let Ok(bytes) = STANDARD.decode(&data) {
                        log_in.debug(
                            "stdin",
                            &[
                                ("pid", json!(pid)),
                                ("bytes", json!(bytes.len())),
                                ("preview", json!(io_preview(&bytes))),
                            ],
                        );
                        if let Some(ref mut w) = *stdin_ref.lock().unwrap() {
                            let _ = w.write_all(&bytes);
                            let _ = w.flush();
                        }
                    }
                }
                ClientMessage::StdinClose => {
                    log_in.debug("stdin closed", &[("pid", json!(pid))]);
                    *stdin_ref.lock().unwrap() = None;
                }
                ClientMessage::Signal { signal } => {
                    if reaped_ref.load(Ordering::Acquire) {
                        break;
                    }
                    log_in.debug("signal", &[("pid", json!(pid)), ("signal", json!(signal))]);
                    unsafe { kill(pid as i32, signal) };
                }
                _ => {}
            }
        }
        // Don't kill child on client disconnect; let it finish naturally.
        // The main thread will reap it with wait() after forwarding threads exit.
    });

    let status = child.wait()?;
    reaped.store(true, Ordering::Release);
    state.unregister_child(pid);

    let _ = stdout_handle.join();
    let _ = stderr_handle.join();

    let code = status.code();
    let mut fields: Vec<(&str, Value)> = vec![("pid", json!(pid)), ("exit_code", json!(code))];
    if !status.success() {
        fields.push(("success", json!(false)));
    }
    log.info("exec finished", &fields);

    let _ = send(&writer, &ServerMessage::Exit { code });

    Ok(())
}

fn forward_output(
    mut source: impl Read,
    writer: &Mutex<UnixStream>,
    is_stderr: bool,
    log: &Logger,
    pid: u32,
) {
    let stream = if is_stderr { "stderr" } else { "stdout" };
    let mut buf = [0u8; 4096];
    loop {
        match source.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let chunk = &buf[..n];
                log.debug(
                    stream,
                    &[
                        ("pid", json!(pid)),
                        ("bytes", json!(n)),
                        ("preview", json!(io_preview(chunk))),
                    ],
                );
                let data = STANDARD.encode(chunk);
                let msg = if is_stderr {
                    ServerMessage::Stderr { data }
                } else {
                    ServerMessage::Stdout { data }
                };
                if send(writer, &msg).is_err() {
                    break;
                }
            }
            Err(ref e) if e.kind() == ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
    }
}

fn send(writer: &Mutex<UnixStream>, msg: &ServerMessage) -> std::io::Result<()> {
    let mut buf = serde_json::to_vec(msg)?;
    buf.push(b'\n');
    writer.lock().unwrap().write_all(&buf)
}

fn io_err(msg: &str) -> std::io::Error {
    std::io::Error::other(msg)
}
