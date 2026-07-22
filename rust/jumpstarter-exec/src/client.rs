use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex};
use std::thread;

use base64::{engine::general_purpose::STANDARD, Engine as _};

use crate::protocol::{ClientMessage, ServerMessage};

pub fn exec(socket_path: &str, argv: Vec<String>) -> std::io::Result<i32> {
    let stream = UnixStream::connect(socket_path)?;
    let reader = BufReader::new(stream.try_clone()?);
    let writer: Arc<Mutex<UnixStream>> = Arc::new(Mutex::new(stream));

    send(
        &writer,
        &ClientMessage::Exec {
            argv,
            env: vec![],
            cwd: None,
        },
    )?;

    // Forward local stdin to the remote child in a background thread.
    let w = Arc::clone(&writer);
    let _stdin_thread = thread::spawn(move || {
        let stdin = std::io::stdin();
        let mut handle = stdin.lock();
        let mut buf = [0u8; 4096];
        loop {
            match handle.read(&mut buf) {
                Ok(0) => {
                    let _ = send(&w, &ClientMessage::StdinClose);
                    break;
                }
                Ok(n) => {
                    let data = STANDARD.encode(&buf[..n]);
                    if send(&w, &ClientMessage::Stdin { data }).is_err() {
                        break;
                    }
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
    });

    let mut exit_code = 1;
    for line in reader.lines() {
        let line = line?;
        let msg: ServerMessage = serde_json::from_str(&line)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        match msg {
            ServerMessage::Started { .. } => {}
            ServerMessage::Stdout { data } => {
                if let Ok(bytes) = STANDARD.decode(&data) {
                    std::io::stdout().write_all(&bytes)?;
                    std::io::stdout().flush()?;
                }
            }
            ServerMessage::Stderr { data } => {
                if let Ok(bytes) = STANDARD.decode(&data) {
                    std::io::stderr().write_all(&bytes)?;
                    std::io::stderr().flush()?;
                }
            }
            ServerMessage::Exit { code } => {
                exit_code = code.unwrap_or(1);
                break;
            }
            ServerMessage::Error { message } => {
                eprintln!("jumpstarter-exec: server error: {message}");
                break;
            }
        }
    }

    Ok(exit_code)
}

fn send(writer: &Mutex<UnixStream>, msg: &ClientMessage) -> std::io::Result<()> {
    let mut buf = serde_json::to_vec(msg)?;
    buf.push(b'\n');
    writer.lock().unwrap().write_all(&buf)
}
