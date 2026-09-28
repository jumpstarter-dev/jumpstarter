"""Ownership and cleanup of a child-process tree.

The native ``ChildProcessTree`` class is currently available only on Windows.
Construct ``ChildProcessTree(handle)`` with the spawned child's process handle
(``subprocess.Popen._handle`` or ``multiprocessing.Process.sentinel``) while the
child is waiting at a startup handshake. Release the handshake only after construction succeeds:
processes created before assignment are not covered.

``close()`` is idempotent and terminates remaining members without waiting for
them to exit. The caller must retain its child-process handle, implement graceful
shutdown and deadlines, and reap the child after cleanup. Assignment failure
raises ``OSError``; terminate the still-gated child instead of continuing
unmanaged. Null and current-process handles are rejected. This API does not
implement spawning, signals, asynchronous scheduling, or protocol behavior.

Windows backend
---------------
The guard owns a private, non-inheritable Job Object using ``KILL_ON_JOB_CLOSE``.
Closing or dropping the guard, or terminating its owning process, closes the
private handle and terminates the remaining child tree. The parent itself is
never assigned to this job, and its existing job policies are not modified.
Incompatible pre-existing job policies remain errors rather than silently
weakening containment. Native handles are not exposed.

Rust consumers use ``jumpstarter_proc::process::ChildProcessTree::new(pid)``
without building the Python extension.
"""

from ._core import ChildProcessTree

__all__ = ["ChildProcessTree"]
