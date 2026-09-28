"""Local IPC primitives backed by the Python-independent ``jumpstarter-ipc`` crate.

These native classes are currently available only on Windows. Async scheduling
and cancellation belong to the caller; operations are nonblocking and own their
native resources.

Socket operations
-----------------
``UnixListener.bind(path)`` never replaces an existing pathname. ``try_accept``
returns a stream or ``None`` when accepting would block. ``UnixStream.connect``
starts a nonblocking connection; ``finish_connect`` returns whether it is ready.
``try_recv`` returns bytes (``b""`` for EOF) or ``None`` when pending, and
``try_send`` returns the number of bytes written or ``None``. Callers must handle
partial writes. ``shutdown_write`` half-closes a stream. Every object has an
idempotent ``close`` method; dropping it also releases its native resources.
``fileno`` returns the socket handle only for readiness waits (for example
``select`` or AnyIO's ``wait_readable``); the object keeps ownership. Stop
waiting on the handle before closing its object.

The Rust backend serializes each socket operation against close and the binding
releases the Python interpreter during native operations. Listener close does
not close accepted streams. Close all sockets before their private directory;
explicit directory close reports cleanup errors, while drop is best effort.

Jumpstarter's Python AnyIO adapter retries these calls when AnyIO reports the
handle ready; on Windows' proactor event loop, AnyIO waits for readiness in one
shared selector thread, so idle sockets cause no wakeups. The Rust crate itself
does not provide an async runtime adapter.

Windows backend
---------------
The backend uses Winsock AF_UNIX streams through ``socket2`` for creation,
non-inheritable handles, nonblocking accept/connect, byte I/O, shutdown, and
cleanup. The socket's pending error and peer address report connection
completion.
The Python API exposes owned objects, not raw socket handles.

``PrivateDirectory.create(base=None)`` creates an unpredictable directory
atomically with a protected DACL granting access only to its owner (the current
user) and SYSTEM, then verifies that DACL. Files created in the directory
inherit entries for their owner's SID and SYSTEM, so it can also hold private
files such as an SSH identity. Its ``socket_path`` is suitable for Winsock and
gRPC. Paths must be absolute, NUL-free UTF-8 and at most 107 bytes; overlong
paths raise an actionable error, and callers can supply a shorter private
runtime-directory base.
"""

from ._core import PrivateDirectory, UnixListener, UnixStream

__all__ = ["PrivateDirectory", "UnixListener", "UnixStream"]
