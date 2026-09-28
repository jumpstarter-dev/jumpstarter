//! A Rust-only consumer: no Python interpreter, bindings, or async runtime.

#[cfg(windows)]
mod smoke {
    use std::io;
    use std::thread;
    use std::time::{Duration, Instant};

    use jumpstarter_ipc::local::{PrivateDirectory, UnixListener, UnixStream};

    fn wait<T>(mut operation: impl FnMut() -> io::Result<Option<T>>) -> io::Result<T> {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(value) = operation()? {
                return Ok(value);
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "local IPC operation timed out",
                ));
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    pub fn run() -> io::Result<()> {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<PrivateDirectory>();
        assert_send_sync::<UnixListener>();
        assert_send_sync::<UnixStream>();

        for _ in 0..4 {
            let directory = PrivateDirectory::create(None)?;
            let path = directory.socket_path().to_path_buf();
            let parent = path.parent().expect("absolute socket parent").to_path_buf();
            let listener = UnixListener::bind(&path)?;
            assert!(listener.try_accept()?.is_none());
            let client = UnixStream::connect(&path)?;
            wait(|| client.finish_connect().map(|ready| ready.then_some(())))?;
            let server = wait(|| listener.try_accept())?;
            let mut read_buffer = [0u8; 4096];
            assert!(server.try_recv(&mut read_buffer)?.is_none());

            let payload: Vec<u8> = (0..256)
                .cycle()
                .take(262144)
                .map(|value| value as u8)
                .collect();
            let mut sent = 0;
            let mut received = Vec::new();
            let deadline = Instant::now() + Duration::from_secs(5);
            while sent < payload.len() || received.len() < payload.len() {
                if Instant::now() >= deadline {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "binary transfer timed out",
                    ));
                }
                if sent < payload.len() {
                    if let Some(count) = client.try_send(&payload[sent..])? {
                        assert!(count > 0);
                        sent += count;
                    }
                }
                if let Some(count) = server.try_recv(&mut read_buffer)? {
                    assert!(count > 0);
                    received.extend_from_slice(&read_buffer[..count]);
                }
            }
            assert_eq!(received, payload);

            // Explicit listener close leaves independently owned streams alive.
            listener.close()?;
            listener.close()?;
            assert!(listener.try_accept().is_err());
            client.shutdown_write()?;
            assert_eq!(wait(|| server.try_recv(&mut read_buffer))?, 0);
            let reply = b"after-eof";
            let mut reply_sent = 0;
            let mut reply_received = Vec::new();
            let deadline = Instant::now() + Duration::from_secs(5);
            while reply_sent < reply.len() || reply_received.len() < reply.len() {
                if Instant::now() >= deadline {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "half-close reply timed out",
                    ));
                }
                if reply_sent < reply.len() {
                    if let Some(count) = server.try_send(&reply[reply_sent..])? {
                        assert!(count > 0);
                        reply_sent += count;
                    }
                }
                if let Some(count) = client.try_recv(&mut read_buffer)? {
                    assert!(count > 0);
                    reply_received.extend_from_slice(&read_buffer[..count]);
                }
            }
            assert_eq!(reply_received, reply);
            server.close()?;
            server.close()?;
            assert!(server.try_send(b"closed").is_err());
            drop(client); // Also prove ordinary Rust drop releases its handle.
            drop(server);
            drop(listener);
            directory.close()?;
            directory.close()?;
            assert!(!path.exists());
            assert!(!parent.exists());
        }
        // The directory itself also owns cleanup through ordinary Rust drop.
        let directory = PrivateDirectory::create(None)?;
        let parent = directory.socket_path().parent().unwrap().to_path_buf();
        drop(directory);
        assert!(!parent.exists());
        println!("Rust-only IPC: binary roundtrips, half-close, ownership, and cleanup passed");
        Ok(())
    }
}

#[cfg(windows)]
fn main() -> std::io::Result<()> {
    smoke::run()
}

#[cfg(not(windows))]
fn main() -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "this IPC example requires Windows",
    ))
}
