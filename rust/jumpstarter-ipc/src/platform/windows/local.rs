//! Safe socket2 owns socket I/O and handles. The only socket FFI is a
//! zero-timeout readiness check for completing a nonblocking connection.
//!
//! Sockets are configured as nonblocking before connect or bind. Accepted
//! sockets are explicitly configured as nonblocking as well. Owned handles
//! are non-inheritable; a mutex prevents close from racing an operation that
//! uses a handle. `select` checks writable and exceptional readiness without
//! waiting, then `take_error` reports a failed connection. Platform socket
//! errors are returned through `std::io::Error`.

use std::io::{self, Read};
use std::net::Shutdown;
use std::os::windows::io::AsRawSocket;
use std::path::Path;
use std::ptr;
use std::sync::Mutex;

use socket2::{Domain, SockAddr, Socket, Type};
use windows_sys::Win32::Networking::WinSock::{
    select, WSAGetLastError, FD_SET, SOCKET_ERROR, TIMEVAL, WSAEALREADY, WSAEINPROGRESS,
    WSAENOTSOCK,
};

use super::{lock, private_directory};

fn closed() -> io::Error {
    io::Error::from_raw_os_error(WSAENOTSOCK)
}

fn socket_address(path: &Path) -> io::Result<SockAddr> {
    private_directory::validate_socket_path(path)?;
    SockAddr::unix(path)
}

/// An owned, nonblocking local stream listener.
///
/// All operations serialize against close. Closing or dropping a listener does
/// not close accepted streams and does not remove its filesystem pathname.
/// Close its streams/listener before closing the owning [`crate::local::PrivateDirectory`].
pub struct UnixListener {
    socket: Mutex<Option<Socket>>,
}

impl UnixListener {
    /// Bind with a backlog of 128. An existing pathname is never removed.
    pub fn bind(path: impl AsRef<Path>) -> io::Result<Self> {
        Self::bind_with_backlog(path, 128)
    }

    /// Bind with a positive backlog. The socket is non-inheritable and
    /// nonblocking before binding; this does not wait for clients.
    pub fn bind_with_backlog(path: impl AsRef<Path>, backlog: i32) -> io::Result<Self> {
        if backlog < 1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "backlog must be at least 1",
            ));
        }
        let address = socket_address(path.as_ref())?;
        let socket = Socket::new(Domain::UNIX, Type::STREAM, None)?;
        socket.set_nonblocking(true)?;
        socket.bind(&address)?;
        socket.listen(backlog)?;
        Ok(Self {
            socket: Mutex::new(Some(socket)),
        })
    }

    /// Accept one independently owned stream, or return `None` if pending.
    pub fn try_accept(&self) -> io::Result<Option<UnixStream>> {
        let guard = lock(&self.socket)?;
        let socket = guard.as_ref().ok_or_else(closed)?;
        match socket.accept() {
            Ok((accepted, _)) => {
                // socket2 disables inheritance; explicitly configure nonblocking
                // instead of relying on Windows inheritance of that setting.
                accepted.set_nonblocking(true)?;
                Ok(Some(UnixStream::new(accepted, true)))
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(error),
        }
    }

    /// Idempotently close this listener. Dropping it also closes its handle.
    pub fn close(&self) -> io::Result<()> {
        lock(&self.socket)?.take();
        Ok(())
    }
}

struct StreamState {
    socket: Socket,
    connected: bool,
}

/// An owned, nonblocking local byte stream, independent of any async runtime.
///
/// The handle is non-inheritable and owned until close/drop. Methods serialize
/// briefly against close; none waits for socket readiness. Callers must retry
/// pending operations, handle partial writes, and impose their own deadlines.
pub struct UnixStream {
    state: Mutex<Option<StreamState>>,
}

impl UnixStream {
    fn new(socket: Socket, connected: bool) -> Self {
        Self {
            state: Mutex::new(Some(StreamState { socket, connected })),
        }
    }

    /// Start a nonblocking connection. Call [`Self::finish_connect`] before I/O.
    pub fn connect(path: impl AsRef<Path>) -> io::Result<Self> {
        let address = socket_address(path.as_ref())?;
        let socket = Socket::new(Domain::UNIX, Type::STREAM, None)?;
        socket.set_nonblocking(true)?;
        let connected = match socket.connect(&address) {
            Ok(()) => true,
            Err(error)
                if error.kind() == io::ErrorKind::WouldBlock
                    || matches!(error.raw_os_error(), Some(WSAEINPROGRESS | WSAEALREADY)) =>
            {
                false
            }
            Err(error) => return Err(error),
        };
        Ok(Self::new(socket, connected))
    }

    /// Check connection completion without waiting; surface the Windows error
    /// on failure and return `false` while the operation remains pending.
    pub fn finish_connect(&self) -> io::Result<bool> {
        let mut guard = lock(&self.state)?;
        let state = guard.as_mut().ok_or_else(closed)?;
        if !state.connected {
            state.connected = connect_ready(&state.socket)?;
        }
        Ok(state.connected)
    }

    /// Read into a caller-owned, nonempty buffer. `None` means pending;
    /// `Some(0)` means EOF, and `Some(n)` identifies the initialized prefix.
    pub fn try_recv(&self, buffer: &mut [u8]) -> io::Result<Option<usize>> {
        if buffer.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "receive buffer must not be empty",
            ));
        }
        let guard = lock(&self.state)?;
        let state = guard.as_ref().ok_or_else(closed)?;
        match (&state.socket).read(buffer) {
            Ok(count) => Ok(Some(count)),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(error),
        }
    }

    /// Write bytes, returning a possibly partial count or `None` if pending.
    pub fn try_send(&self, data: &[u8]) -> io::Result<Option<usize>> {
        let guard = lock(&self.state)?;
        let state = guard.as_ref().ok_or_else(closed)?;
        match state.socket.send(data) {
            Ok(count) => Ok(Some(count)),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(error),
        }
    }

    /// Half-close writes while allowing the peer's remaining data to be read.
    pub fn shutdown_write(&self) -> io::Result<()> {
        let guard = lock(&self.state)?;
        guard
            .as_ref()
            .ok_or_else(closed)?
            .socket
            .shutdown(Shutdown::Write)
    }

    /// Idempotently close this stream. Dropping it also closes its handle.
    pub fn close(&self) -> io::Result<()> {
        lock(&self.state)?.take();
        Ok(())
    }
}

fn connect_ready(socket: &Socket) -> io::Result<bool> {
    let mut writable = FD_SET {
        fd_count: 1,
        fd_array: [0; 64],
    };
    writable.fd_array[0] = socket.as_raw_socket() as usize;
    let mut failed = writable;
    let timeout = TIMEVAL {
        tv_sec: 0,
        tv_usec: 0,
    };
    // SAFETY: the socket remains owned and locked by StreamState throughout
    // this call. Both sets contain one valid handle and the timeout is zero;
    // select never waits or outlives these stack allocations.
    let result = unsafe { select(0, ptr::null_mut(), &mut writable, &mut failed, &timeout) };
    if result == SOCKET_ERROR {
        // SAFETY: retrieves the calling thread's error immediately after select.
        return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    if result == 0 {
        return Ok(false);
    }
    if let Some(error) = socket.take_error()? {
        return Err(error);
    }
    if failed.fd_count != 0 {
        return Err(io::Error::other(
            "local socket connection failed without an error code",
        ));
    }
    Ok(writable.fd_count != 0)
}
