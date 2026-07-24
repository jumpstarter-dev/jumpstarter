use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};

use crate::log::{io_preview, LogFormat, Logger};
use crate::protocol::{ClientMessage, ServerMessage};

extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
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

/// Listen on `socket_path` with default options (JSON logs, debug off).
pub fn serve(socket_path: &str) -> std::io::Result<()> {
    serve_with(socket_path, ServeOptions::default())
}

/// Listen on `socket_path` with the given options.
pub fn serve_with(socket_path: &str, opts: ServeOptions) -> std::io::Result<()> {
    let log = Arc::new(Logger::new(opts.log_format, opts.debug, opts.log_fields));

    let _ = std::fs::remove_file(socket_path);
    let listener = UnixListener::bind(socket_path)?;
    log.info(
        "listening",
        &[("socket", json!(socket_path)), ("debug", json!(opts.debug))],
    );

    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                let log = Arc::clone(&log);
                thread::spawn(move || {
                    if let Err(e) = handle_connection(s, Arc::clone(&log)) {
                        log.error("connection error", &[("error", json!(e.to_string()))]);
                    }
                });
            }
            Err(e) => log.error("accept error", &[("error", json!(e.to_string()))]),
        }
    }
    Ok(())
}

fn handle_connection(stream: UnixStream, log: Arc<Logger>) -> std::io::Result<()> {
    let reader = BufReader::new(stream.try_clone()?);
    let writer: Arc<Mutex<UnixStream>> = Arc::new(Mutex::new(stream));
    let mut lines = reader.lines();

    let first_line = lines
        .next()
        .ok_or_else(|| io_err("client disconnected before sending a request"))??;

    let msg: ClientMessage =
        serde_json::from_str(&first_line).map_err(|e| io_err(&format!("invalid message: {e}")))?;

    let (argv, env, cwd) = match msg {
        ClientMessage::Exec { argv, env, cwd } => (argv, env, cwd),
        _ => return Err(io_err("first message must be Exec")),
    };

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

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let msg = format!("spawn failed: {e}");
            log.error(
                "spawn failed",
                &[("argv", json!(argv)), ("error", json!(e.to_string()))],
            );
            let _ = send(
                &writer,
                &ServerMessage::Error {
                    message: msg.clone(),
                },
            );
            return Err(io_err(&msg));
        }
    };

    let pid = child.id();
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

    // Read client messages and dispatch to child stdin / signals.
    // If the client disconnects, kill the child so wait() returns.
    let child_stdin = Arc::new(Mutex::new(child_stdin));
    let stdin_ref = Arc::clone(&child_stdin);
    let log_in = Arc::clone(&log);
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
                    log_in.debug("signal", &[("pid", json!(pid)), ("signal", json!(signal))]);
                    unsafe { kill(pid as i32, signal) };
                }
                _ => {}
            }
        }
        unsafe { kill(pid as i32, 15) }; // SIGTERM on client disconnect
    });

    let status = child.wait()?;

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
            Err(ref e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
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
