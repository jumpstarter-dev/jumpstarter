use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use base64::{engine::general_purpose::STANDARD, Engine as _};

use crate::protocol::{ClientMessage, ServerMessage};

extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
}

pub fn serve(socket_path: &str) -> std::io::Result<()> {
    let _ = std::fs::remove_file(socket_path);
    let listener = UnixListener::bind(socket_path)?;
    eprintln!("jumpstarter-exec: listening on {socket_path}");

    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                thread::spawn(move || {
                    if let Err(e) = handle_connection(s) {
                        eprintln!("jumpstarter-exec: connection error: {e}");
                    }
                });
            }
            Err(e) => eprintln!("jumpstarter-exec: accept error: {e}"),
        }
    }
    Ok(())
}

fn handle_connection(stream: UnixStream) -> std::io::Result<()> {
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
    send(&writer, &ServerMessage::Started { pid })?;

    let child_stdin = child.stdin.take();
    let child_stdout = child.stdout.take().unwrap();
    let child_stderr = child.stderr.take().unwrap();

    let w = Arc::clone(&writer);
    let stdout_handle = thread::spawn(move || forward_output(child_stdout, &w, false));

    let w = Arc::clone(&writer);
    let stderr_handle = thread::spawn(move || forward_output(child_stderr, &w, true));

    // Read client messages and dispatch to child stdin / signals.
    // If the client disconnects, kill the child so wait() returns.
    let child_stdin = Arc::new(Mutex::new(child_stdin));
    let stdin_ref = Arc::clone(&child_stdin);
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
                        if let Some(ref mut w) = *stdin_ref.lock().unwrap() {
                            let _ = w.write_all(&bytes);
                            let _ = w.flush();
                        }
                    }
                }
                ClientMessage::StdinClose => {
                    *stdin_ref.lock().unwrap() = None;
                }
                ClientMessage::Signal { signal } => {
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

    let _ = send(
        &writer,
        &ServerMessage::Exit {
            code: status.code(),
        },
    );

    Ok(())
}

fn forward_output(mut source: impl Read, writer: &Mutex<UnixStream>, is_stderr: bool) {
    let mut buf = [0u8; 4096];
    loop {
        match source.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let data = STANDARD.encode(&buf[..n]);
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
