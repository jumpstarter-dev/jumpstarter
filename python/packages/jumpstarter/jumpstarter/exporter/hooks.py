"""Lifecycle hooks for Jumpstarter exporters."""

import codecs
import logging
import ntpath
import os
import select
import shutil
import stat
import sys
import tempfile
import threading
import time
from base64 import b64encode
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import anyio
import anyio.lowlevel
from anyio import CancelScope, to_thread

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus, LogSource
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_MOTD_FILE, JUMPSTARTER_HOST
from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.exporter.session import Session

if TYPE_CHECKING:
    from jumpstarter.exporter.lease_context import LeaseContext

logger = logging.getLogger(__name__)

MAX_DRAIN_BYTES = 256 * 1024
DRAIN_TIMEOUT_SECONDS = 2.0
DRAIN_MAX_EMPTY_POLLS = 10

# Upper bound on hook-contributed motd content read from $JMP_MOTD_FILE.
MAX_MOTD_BYTES = 64 * 1024

# Module-level reference to time.monotonic so tests can patch it without
# affecting the asyncio event loop (which also uses time.monotonic).
_monotonic = time.monotonic


def _flush_lines(buffer: bytes, output_lines: list[str]) -> bytes:
    """Extract and log complete lines from a byte buffer.

    Splits the buffer on newline boundaries, decodes each complete line,
    and appends non-empty lines to output_lines while logging them.

    Returns the remaining bytes after the last newline (incomplete line).
    """
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        line_decoded = line.decode(errors="replace").rstrip()
        if line_decoded:
            output_lines.append(line_decoded)
            logger.info("%s", line_decoded)
    return buffer


def _decode_hook_text(data: bytes) -> str:
    """Decode hook-written text, honoring the BOM Windows PowerShell may write."""
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(encoding, errors="replace")
    return data.decode(errors="replace")


# Hooks run without a console, so PowerShell would otherwise emit output and
# write files such as $JMP_MOTD_FILE in legacy code pages.
_POWERSHELL_HOOK_PREAMBLE = """\
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$ProgressPreference = 'SilentlyContinue'
"""


def _windows_bash() -> str | None:
    """Find a native Bash such as Git Bash, never the WSL launcher.

    WSL's bash.exe runs a Linux shell that cannot use the exporter's Windows
    socket path or its j executable.
    """
    candidates = []
    if git := shutil.which("git"):
        root = Path(git).resolve().parent.parent
        candidates += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    if bash := shutil.which("bash"):
        candidates.append(Path(bash))
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    wsl_launchers = {windows / name for name in ("System32", "Sysnative", "SysWOW64")}
    for candidate in candidates:
        wsl = candidate.parent in wsl_launchers or "windowsapps" in str(candidate).lower()
        if candidate.is_file() and not wsl:
            return str(candidate)
    return None


def _powershell_hook_command(executable: str, script: str, is_file: bool) -> list[str]:
    # Text output keeps redirected streams free of CLIXML progress records.
    command = [
        executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-ExecutionPolicy", "Bypass",
    ]
    if is_file:
        return [*command, "-File", script]
    # EncodedCommand avoids Windows command-line quoting of multi-line scripts.
    encoded = b64encode((_POWERSHELL_HOOK_PREAMBLE + script).encode("utf-16-le")).decode("ascii")
    return [*command, "-EncodedCommand", encoded]


def _windows_hook_command(hook_config: HookInstanceConfigV1Alpha1) -> list[str]:
    """Select the hook interpreter on Windows.

    PowerShell runs inline and .ps1 hooks by default. Python hooks use the
    exporter's interpreter, .cmd/.bat files use cmd.exe, and a native Bash such
    as Git Bash runs .sh files or hooks whose exec is bash or sh (including
    POSIX paths such as /bin/sh), so Linux hook configurations remain usable.
    """
    script = hook_config.script.strip()
    is_file = "\n" not in script and os.path.isfile(script)
    extension = os.path.splitext(script)[1].lower() if is_file else ""
    interpreter = hook_config.exec_
    if interpreter is None:
        if extension == ".py":
            logger.debug("Auto-detected Python script: %s (interpreter: %s)", script, sys.executable)
            interpreter = sys.executable
        elif extension in (".bat", ".cmd"):
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", script]
        elif extension == ".sh":
            interpreter = "bash"
        else:
            powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
            logger.debug("Executing %s with PowerShell: %s", "script file" if is_file else "inline script", powershell)
            return _powershell_hook_command(powershell, script, is_file)

    name = ntpath.basename(interpreter).lower().removesuffix(".exe")
    if name in ("pwsh", "powershell"):
        return _powershell_hook_command(interpreter, script, is_file)
    if name in ("bash", "sh") and not os.path.isfile(interpreter):
        bash = _windows_bash()
        if bash is None:
            raise RuntimeError("Hook requires Bash, but no native Bash (such as Git Bash) was found")
        interpreter = bash
    elif name in ("python", "python3") and not os.path.isfile(interpreter):
        # Bare Python names mean the exporter's own interpreter, not a Store alias.
        interpreter = sys.executable

    if is_file:
        logger.debug("Executing script file: %s (interpreter: %s)", script, interpreter)
        return [interpreter, script]
    logger.debug("Executing inline script (interpreter: %s)", interpreter)
    return [interpreter, "-c", hook_config.script]


def _log_hook_output(stream) -> None:
    """Log complete lines from a Windows hook's output pipe until it closes."""
    buffer = b""
    output_lines: list[str] = []
    try:
        while chunk := stream.read1(4096):
            buffer = _flush_lines(buffer + chunk, output_lines)
    except (OSError, ValueError):
        pass
    if line := buffer.decode(errors="replace").rstrip():
        logger.info("%s", line)


def _kill_hook_process_tree(process) -> None:
    """Terminate a Windows hook and the commands it started."""
    import subprocess

    taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
    subprocess.run([taskkill, "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    if process.poll() is None:
        process.kill()
    process.wait()


@dataclass
class HookExecutionError(Exception):
    """Raised when a hook fails and on_failure is set to 'endLease' or 'exit'.

    Attributes:
        message: Error message describing the failure
        on_failure: The on_failure mode that triggered this error ('endLease' or 'exit')
        hook_type: The type of hook that failed ('before_lease' or 'after_lease')
    """

    message: str
    on_failure: Literal["endLease", "exit"]
    hook_type: Literal["before_lease", "after_lease"]

    def __str__(self) -> str:
        return self.message

    def should_shutdown_exporter(self) -> bool:
        """Returns True if the exporter should be shut down entirely."""
        return self.on_failure == "exit"

    def should_end_lease(self) -> bool:
        """Returns True if the lease should be ended."""
        return self.on_failure in ("endLease", "exit")


@dataclass
class PtyState:
    """Mutable state for PTY file descriptors and reader coordination.

    Tracks which fds are still open (for cleanup) and provides a separate
    stop flag to signal the reader task without affecting fd lifecycle.
    """

    parent_fd_open: bool = True
    child_fd_open: bool = True
    reader_stop: bool = False


@dataclass(kw_only=True)
class HookExecutor:
    """Executes lifecycle hooks with access to the j CLI."""

    config: HookConfigV1Alpha1

    def _create_hook_env(self, lease_scope: "LeaseContext") -> dict[str, str]:
        """Create standardized hook environment variables.

        Args:
            lease_scope: LeaseScope containing lease metadata and socket paths

        Returns:
            Dictionary of environment variables for hook execution

        Note:
            Uses the hook_socket_path (if available) instead of the main socket_path
            to prevent SSL frame corruption when hook j commands access the session
            concurrently with client LogStream connections.
        """
        hook_env = os.environ.copy()
        # Use dedicated hook socket to prevent SSL corruption
        # Falls back to main socket if hook socket not available (backward compatibility)
        socket_path = lease_scope.hook_socket_path or lease_scope.socket_path
        if lease_scope.hook_socket_path:
            logger.info(
                "Using dedicated hook socket: %s (main socket: %s)",
                lease_scope.hook_socket_path,
                lease_scope.socket_path,
            )
        else:
            logger.warning(
                "No dedicated hook socket available, using main socket: %s "
                "(may cause SSL issues if client is connected)",
                socket_path,
            )
        hook_env.update(
            {
                JUMPSTARTER_HOST: str(socket_path),
                JMP_DRIVERS_ALLOW: "UNSAFE",  # Allow all drivers for local access
                "LEASE_NAME": lease_scope.lease_name,
                "CLIENT_NAME": lease_scope.client_name,
                # Signal noninteractive mode to the child process.
                # Even though hooks run in a PTY (for line-buffered output), they
                # are not interactive sessions. These variables prevent programs
                # from displaying prompts or interactive UI.
                "TERM": "dumb",
                "DEBIAN_FRONTEND": "noninteractive",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        # Remove PS1 so the shell does not emit a prompt
        hook_env.pop("PS1", None)
        return hook_env

    async def _execute_hook(
        self,
        hook_config: HookInstanceConfigV1Alpha1,
        lease_scope: "LeaseContext",
        log_source: LogSource,
    ) -> str | None:
        """Execute a single hook command.

        Args:
            hook_config: Hook configuration including script, timeout, and on_failure
            lease_scope: LeaseScope containing lease metadata and session
            log_source: Log source for hook output

        Returns:
            Warning message string if hook failed with on_failure='warn', None otherwise
        """
        command = hook_config.script
        if not command or not command.strip():
            logger.debug("Hook command is empty, skipping")
            return None

        logger.debug("Executing hook: %s", command.strip().split("\n")[0][:100])

        # Determine hook type from log source
        hook_type = "before_lease" if log_source == LogSource.BEFORE_LEASE_HOOK else "after_lease"

        # Validate session is available for logging
        if lease_scope.session is None:
            raise RuntimeError("Cannot execute hook: lease_scope.session is None")

        # Use existing session from lease_scope
        hook_env = self._create_hook_env(lease_scope)
        logger.debug(
            "Hook environment: JUMPSTARTER_HOST=%s, LEASE_NAME=%s, CLIENT_NAME=%s",
            hook_env.get("JUMPSTARTER_HOST", "NOT_SET"),
            hook_env.get("LEASE_NAME", "NOT_SET"),
            hook_env.get("CLIENT_NAME", "NOT_SET"),
        )

        # $JMP_MOTD_FILE is beforeLease-only; clear any inherited value first
        hook_env.pop(JMP_MOTD_FILE, None)
        motd_file = None
        if hook_type == "before_lease":
            fd, motd_file = tempfile.mkstemp(prefix="jmp-motd-")
            os.close(fd)
            hook_env[JMP_MOTD_FILE] = motd_file

        try:
            result = await self._execute_hook_process(
                hook_config, lease_scope, log_source, hook_env, lease_scope.session, hook_type
            )
            if motd_file:
                self._append_hook_motd(lease_scope.session, motd_file)
            return result
        finally:
            if motd_file:
                try:
                    os.unlink(motd_file)
                except OSError:
                    pass

    @staticmethod
    def _append_hook_motd(session: Session, motd_file: str) -> None:
        """Append hook-written motd content to the session motd.

        The hook controls this path: O_NONBLOCK avoids hanging on a FIFO,
        non-regular files are skipped, and the read is capped.
        """
        try:
            # Windows has neither FIFOs nor O_NONBLOCK.
            fd = os.open(motd_file, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except OSError as e:
            logger.warning("Failed to open hook motd file %s: %s", motd_file, e)
            return
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                logger.warning("Hook motd file %s is not a regular file, ignoring", motd_file)
                return
            content = _decode_hook_text(os.read(fd, MAX_MOTD_BYTES)).strip()
        except OSError as e:
            logger.warning("Failed to read hook motd file %s: %s", motd_file, e)
            return
        finally:
            os.close(fd)
        if content:
            session.motd = "\n".join(filter(None, [session.motd, content]))

    def _handle_hook_failure(
        self,
        error_msg: str,
        on_failure: Literal["warn", "endLease", "exit"],
        hook_type: Literal["before_lease", "after_lease"],
        cause: Exception | None = None,
    ) -> str | None:
        """Handle hook failure according to on_failure setting.

        Args:
            error_msg: Error message describing the failure
            on_failure: The on_failure mode ('warn', 'endLease', or 'exit')
            hook_type: The type of hook that failed
            cause: Optional exception that caused the failure

        Returns:
            Warning message string if on_failure is 'warn', None otherwise

        Raises:
            HookExecutionError: If on_failure is 'endLease' or 'exit'
        """
        if on_failure == "warn":
            logger.warning("%s (on_failure=warn, continuing)", error_msg)
            return error_msg

        logger.exception("%s (on_failure=%s, raising exception)", error_msg, on_failure)

        error = HookExecutionError(
            message=error_msg,
            on_failure=on_failure,
            hook_type=hook_type,
        )

        # Properly handle exception chaining
        if cause is not None:
            raise error from cause
        else:
            raise error

    async def _execute_hook_process(  # noqa: C901
        self,
        hook_config: HookInstanceConfigV1Alpha1,
        lease_scope: "LeaseContext",
        log_source: LogSource,
        hook_env: dict[str, str],
        logging_session: Session,
        hook_type: Literal["before_lease", "after_lease"],
    ) -> str | None:
        """Execute the hook process with the given environment and logging session.

        Uses subprocess with a PTY to force line buffering in the subprocess,
        ensuring logs stream in real-time rather than being block-buffered.

        Returns:
            Warning message string if hook failed with on_failure='warn', None otherwise
        """
        if sys.platform == "win32":
            return await self._execute_hook_process_windows(
                hook_config, log_source, hook_env, logging_session, hook_type
            )

        import pty
        import subprocess

        command = hook_config.script
        timeout = hook_config.timeout
        on_failure = hook_config.on_failure

        # Exception handling
        error_msg: str | None = None
        cause: Exception | None = None
        timed_out = False

        # Route hook output logs to the client via the session's log stream
        logger.debug("Entering log source context for %s", log_source)
        with logging_session.context_log_source(__name__, log_source):
            # Create a PTY pair - this forces line buffering in the subprocess
            logger.debug("Starting hook subprocess...")
            logger.debug("Creating PTY pair...")
            try:
                parent_fd, child_fd = pty.openpty()
            except Exception as e:
                logger.error("Failed to create PTY: %s", e)
                raise
            logger.debug("PTY created: parent_fd=%d, child_fd=%d", parent_fd, child_fd)

            pty_state = PtyState()

            process: subprocess.Popen | None = None
            try:
                # Use subprocess.Popen with the PTY child as stdin/stdout/stderr
                # This avoids the issues with os.fork() in async contexts
                # Determine interpreter and invocation mode
                script_stripped = command.strip()
                is_file = "\n" not in script_stripped and os.path.isfile(script_stripped)

                interpreter = hook_config.exec_
                if is_file and interpreter is None:
                    # Auto-detect interpreter from file extension
                    ext = os.path.splitext(script_stripped)[1].lower()
                    if ext == ".py":
                        interpreter = sys.executable
                        logger.debug("Auto-detected Python script: %s (interpreter: %s)", script_stripped, interpreter)
                    else:
                        interpreter = "/bin/sh"
                        logger.debug("Detected script file: %s (interpreter: %s)", script_stripped, interpreter)
                elif interpreter is None:
                    interpreter = "/bin/sh"

                if is_file:
                    logger.debug("Executing script file: %s (interpreter: %s)", script_stripped, interpreter)
                    cmd = [interpreter, script_stripped]
                else:
                    logger.debug("Executing inline script (interpreter: %s)", interpreter)
                    cmd = [interpreter, "-c", command]

                logger.debug("Spawning subprocess with command: %s", cmd)
                try:
                    process = subprocess.Popen(  # noqa: ASYNC220
                        cmd,
                        stdin=child_fd,
                        stdout=child_fd,
                        stderr=child_fd,
                        env=hook_env,
                        start_new_session=True,  # Equivalent to os.setsid()
                        close_fds=True,  # Close inherited fds to prevent interference with gRPC connections
                    )
                except Exception:
                    logger.exception("Failed to spawn subprocess")
                    raise
                logger.debug("Subprocess spawned with PID %d", process.pid)
                # Close child fd in parent process - subprocess has it now
                os.close(child_fd)
                pty_state.child_fd_open = False
                logger.debug("Closed child_fd in parent process")

                output_lines: list[str] = []

                # Set parent fd to non-blocking mode
                import fcntl

                flags = fcntl.fcntl(parent_fd, fcntl.F_GETFL)
                fcntl.fcntl(parent_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                logger.debug("Parent fd set to non-blocking")

                async def read_pty_output() -> None:  # noqa: C901
                    """Read from PTY parent fd line by line using non-blocking I/O."""
                    logger.debug("read_pty_output task started")
                    buffer = b""
                    read_count = 0
                    last_heartbeat = 0

                    start_time = _monotonic()
                    try:
                        while not pty_state.reader_stop:
                            try:
                                # Wait for fd to be readable with timeout
                                with anyio.move_on_after(0.1):
                                    await anyio.wait_readable(parent_fd)

                                # Check stop flag immediately after timeout
                                # (main task may have signaled us to stop)
                                if pty_state.reader_stop:
                                    logger.debug("read_pty_output: stop flag set, exiting")
                                    break

                                read_count += 1
                                # Log heartbeat every 2 seconds
                                elapsed = _monotonic() - start_time
                                if elapsed - last_heartbeat >= 2.0:
                                    logger.debug(
                                        "read_pty_output: heartbeat at %.1fs, iterations=%d", elapsed, read_count
                                    )
                                    last_heartbeat = elapsed

                                # Read available data (non-blocking)
                                try:
                                    chunk = os.read(parent_fd, 4096)
                                    if not chunk:
                                        # EOF
                                        logger.debug("read_pty_output: EOF received")
                                        break
                                    buffer += chunk
                                except BlockingIOError:
                                    # No data available right now, continue loop
                                    continue
                                except OSError as e:
                                    # PTY closed or error
                                    logger.debug("read_pty_output: OSError on read: %s", e)
                                    break

                                # Process complete lines
                                buffer = _flush_lines(buffer, output_lines)

                            except OSError as e:
                                # PTY closed or read error
                                logger.debug("read_pty_output: OSError in loop: %s", e)
                                break

                            except Exception as e:  # noqa: BLE001
                                logger.debug("read_pty_output: unexpected error in loop: %s", e)
                                break

                    finally:
                        # Drain any remaining data from the PTY buffer.
                        # On macOS, PTY output may still be in the kernel buffer
                        # after the subprocess exits and the stop flag is set.
                        # Use select() with a timeout to poll for readability
                        # instead of immediately breaking on BlockingIOError,
                        # giving the macOS PTY kernel buffer time to deliver
                        # remaining data.
                        # Bound the drain to prevent spinning indefinitely if a
                        # grandchild process holds the PTY slave fd open.
                        try:
                            drain_deadline = _monotonic() + DRAIN_TIMEOUT_SECONDS
                            drained = 0
                            consecutive_empty = 0
                            while drained < MAX_DRAIN_BYTES and _monotonic() < drain_deadline:
                                # Poll for readability with a short timeout.
                                # This avoids the race where a non-blocking read
                                # raises BlockingIOError because the macOS PTY
                                # kernel buffer hasn't delivered the data yet.
                                remaining = drain_deadline - _monotonic()
                                if remaining <= 0:
                                    break
                                timeout_s = min(remaining, 0.1)
                                try:
                                    readable, _, _ = select.select([parent_fd], [], [], timeout_s)
                                except (ValueError, OSError):
                                    # fd closed or invalid
                                    break
                                if not readable:
                                    # On macOS, data may not be available on the
                                    # first select() call even though the subprocess
                                    # has already written and exited.  Keep retrying
                                    # until we see several consecutive empty polls,
                                    # which indicates the buffer is truly drained.
                                    consecutive_empty += 1
                                    if consecutive_empty >= DRAIN_MAX_EMPTY_POLLS:
                                        break
                                    continue
                                consecutive_empty = 0
                                try:
                                    chunk = os.read(parent_fd, 4096)
                                    if not chunk:
                                        break
                                    buffer += chunk
                                    drained += len(chunk)
                                except (BlockingIOError, OSError):
                                    break

                            buffer = _flush_lines(buffer, output_lines)
                        except Exception:
                            logger.debug("read_pty_output: error during drain", exc_info=True)

                        logger.debug("read_pty_output: exiting, processed %d iterations", read_count)
                        if buffer:
                            line_decoded = buffer.decode(errors="replace").rstrip()
                            if line_decoded:
                                output_lines.append(line_decoded)
                                logger.info("%s", line_decoded)

                async def wait_for_process() -> int:
                    """Wait for the subprocess to complete.

                    Ensures the subprocess is properly reaped even if cancelled,
                    preventing zombie processes.
                    """
                    logger.debug("wait_for_process: waiting for PID %d", process.pid)
                    try:
                        result = await to_thread.run_sync(process.wait, abandon_on_cancel=True)
                        logger.debug("wait_for_process: PID %d exited with code %d", process.pid, result)
                        return result
                    finally:
                        # Ensure subprocess is reaped on cancellation to prevent zombies
                        if process.poll() is None:
                            logger.debug("wait_for_process: cleaning up still-running PID %d", process.pid)
                            try:
                                process.terminate()
                                # Give it a moment to terminate gracefully
                                for _ in range(10):
                                    if process.poll() is not None:
                                        break
                                    await anyio.sleep(0.1)
                                # Force kill if still running
                                if process.poll() is None:
                                    logger.debug("wait_for_process: force killing PID %d", process.pid)
                                    process.kill()
                                # Final reap with non-abandoning wait
                                await to_thread.run_sync(process.wait, abandon_on_cancel=False)
                            except Exception as e:  # noqa: BLE001
                                logger.debug("wait_for_process: error during cleanup: %s", e)

                # Use move_on_after for timeout
                returncode: int | None = None
                logger.debug("Starting PTY output reader and process waiter (timeout=%d)", timeout)

                # Yield to event loop to ensure other tasks can progress
                # This helps prevent race conditions in task scheduling
                await anyio.lowlevel.checkpoint()

                with anyio.move_on_after(timeout) as cancel_scope:
                    # Run output reading and process waiting concurrently
                    async with anyio.create_task_group() as tg:
                        logger.debug("Task group created, starting tasks...")
                        tg.start_soon(read_pty_output)
                        logger.debug("Waiting for subprocess to complete...")
                        returncode = await wait_for_process()
                        logger.debug("Subprocess completed with code: %s", returncode)
                        # Give a brief moment for any final output to be read
                        await anyio.sleep(0.2)
                        # Signal the read task to stop via the dedicated stop flag.
                        # The read task checks this flag after each 0.1s timeout
                        # and also receives EOF when the subprocess exits.
                        # Note: pty_state.parent_fd_open stays True so the finally block
                        # properly closes parent_fd.
                        pty_state.reader_stop = True
                        logger.debug("Stop flag set, waiting for read task to exit")
                        # Don't cancel - let the task exit naturally via EOF or flag check
                        # Cancellation can cause unexpected side effects on gRPC connections

                if cancel_scope.cancelled_caught:
                    timed_out = True
                    error_msg = f"Hook timed out after {timeout} seconds"
                    logger.exception(error_msg)
                    # Terminate the process
                    if process and process.poll() is None:
                        process.terminate()
                        # Give it a moment to terminate gracefully
                        with suppress(Exception):
                            with anyio.move_on_after(5):
                                await to_thread.run_sync(process.wait, abandon_on_cancel=True)
                        # Force kill if still running
                        if process.poll() is None:
                            process.kill()
                            with suppress(Exception):
                                await to_thread.run_sync(process.wait, abandon_on_cancel=True)

                elif returncode == 0:
                    logger.debug("Hook executed successfully")
                    return None
                else:
                    error_msg = f"Hook failed with exit code {returncode}"

            except Exception as e:  # noqa: BLE001
                error_msg = f"Error executing hook: {e}"
                cause = e
                logger.error(error_msg)
            finally:
                # Clean up file descriptors - only close those still open to avoid
                # closing an unrelated fd that reused the same number.
                if pty_state.parent_fd_open:
                    try:
                        os.close(parent_fd)
                    except OSError:
                        pass
                if pty_state.child_fd_open:
                    try:
                        os.close(child_fd)
                    except OSError:
                        pass

            # Handle failure inside context_log_source so the WARNING log is
            # routed to the client as a hook log (visible without --exporter-logs).
            if error_msg is not None:
                # For timeout, create a TimeoutError as the cause
                if timed_out and cause is None:
                    cause = TimeoutError(error_msg)
                return self._handle_hook_failure(error_msg, on_failure, hook_type, cause)
        return None

    async def _execute_hook_process_windows(
        self,
        hook_config: HookInstanceConfigV1Alpha1,
        log_source: LogSource,
        hook_env: dict[str, str],
        logging_session: Session,
        hook_type: Literal["before_lease", "after_lease"],
    ) -> str | None:
        """Execute the hook process on Windows, which has no PTY.

        A daemon thread logs output lines from a pipe, so a background process
        that keeps the pipe open cannot block the exporter. Python children are
        unbuffered and UTF-8 so j output still streams line by line.
        """
        import subprocess

        timeout = hook_config.timeout
        error_msg: str | None = None
        cause: Exception | None = None
        process: subprocess.Popen | None = None
        reader: threading.Thread | None = None
        env = {"PYTHONIOENCODING": "utf-8"} | hook_env | {"PYTHONUNBUFFERED": "1"}

        with logging_session.context_log_source(__name__, log_source):
            try:
                cmd = _windows_hook_command(hook_config)
                logger.debug("Spawning subprocess with command: %s", cmd)
                process = subprocess.Popen(  # noqa: ASYNC220
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    # Like start_new_session on POSIX: console Ctrl+C stays with the exporter.
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                reader = threading.Thread(target=_log_hook_output, args=(process.stdout,), daemon=True)
                reader.start()
                returncode: int | None = None
                with anyio.move_on_after(timeout) as timeout_scope:
                    returncode = await to_thread.run_sync(process.wait, abandon_on_cancel=True)
                if timeout_scope.cancelled_caught:
                    error_msg = f"Hook timed out after {timeout} seconds"
                    cause = TimeoutError(error_msg)
                    logger.error(error_msg)
                    await to_thread.run_sync(_kill_hook_process_tree, process)
                elif returncode != 0:
                    error_msg = f"Hook failed with exit code {returncode}"
                # Descendants that outlive the hook may hold the pipe open.
                await to_thread.run_sync(reader.join, DRAIN_TIMEOUT_SECONDS)
                if error_msg is None:
                    logger.debug("Hook executed successfully")
                    return None
            except Exception as e:  # noqa: BLE001
                error_msg = f"Error executing hook: {e}"
                cause = e
                logger.error(error_msg)
            finally:
                if process is not None:
                    if process.poll() is None:
                        _kill_hook_process_tree(process)
                    if reader is None or not reader.is_alive():
                        process.stdout.close()

            return self._handle_hook_failure(error_msg, hook_config.on_failure, hook_type, cause)

    async def execute_before_lease_hook(self, lease_scope: "LeaseContext") -> str | None:
        """Execute the before-lease hook.

        Args:
            lease_scope: LeaseScope with lease metadata and session

        Returns:
            Warning message string if hook failed with on_failure='warn', None otherwise

        Raises:
            HookExecutionError: If hook fails and on_failure is set to 'endLease' or 'exit'
        """
        if not self.config.before_lease:
            logger.debug("No before-lease hook configured")
            return None

        logger.info("Executing before-lease hook for lease %s", lease_scope.lease_name)
        return await self._execute_hook(
            self.config.before_lease,
            lease_scope,
            LogSource.BEFORE_LEASE_HOOK,
        )

    async def execute_after_lease_hook(self, lease_scope: "LeaseContext") -> str | None:
        """Execute the after-lease hook.

        Args:
            lease_scope: LeaseScope with lease metadata and session

        Returns:
            Warning message string if hook failed with on_failure='warn', None otherwise

        Raises:
            HookExecutionError: If hook fails and on_failure is set to 'endLease' or 'exit'
        """
        if not self.config.after_lease:
            logger.debug("No after-lease hook configured")
            return None

        logger.info("Executing after-lease hook for lease %s", lease_scope.lease_name)
        return await self._execute_hook(
            self.config.after_lease,
            lease_scope,
            LogSource.AFTER_LEASE_HOOK,
        )

    async def _safe_release_lease(
        self,
        request_lease_release: Callable[[], Awaitable[None]] | None,
    ) -> None:
        """Call request_lease_release if provided, logging any errors."""
        if request_lease_release:
            try:
                await request_lease_release()
            except Exception:
                logger.exception("Failed to request lease release")

    async def _wait_for_lease_ready(
        self,
        lease_scope: "LeaseContext",
        report_status: Callable[["ExporterStatus", str], Awaitable[None]],
    ) -> bool:
        """Wait for lease scope to be fully populated by handle_lease.

        Returns True if ready, False if the caller should bail out
        (lease already ended or timeout waiting for session).
        """
        timeout = 30  # seconds
        interval = 0.1  # seconds
        elapsed = 0.0
        while not lease_scope.is_ready():
            if lease_scope.lease_ended.is_set():
                logger.info(
                    "Lease %s ended while waiting for session, skipping beforeLease hook",
                    lease_scope.lease_name,
                )
                lease_scope.skip_after_lease_hook = True
                return False
            if elapsed >= timeout:
                error_msg = "Timeout waiting for lease scope to be ready"
                logger.exception(error_msg)
                await report_status(ExporterStatus.BEFORE_LEASE_HOOK_FAILED, error_msg)
                lease_scope.before_lease_hook.set()
                return False
            await anyio.sleep(interval)
            elapsed += interval
        return True

    async def run_before_lease_hook(
        self,
        lease_scope: "LeaseContext",
        report_status: Callable[["ExporterStatus", str], Awaitable[None]],
        shutdown: Callable[..., None],
        request_lease_release: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Execute before-lease hook with full orchestration.

        This method handles the complete lifecycle of running a before-lease hook:
        - Waits for the lease scope to be ready (session/socket populated)
        - Reports status changes via the provided callback
        - Sets up the hook executor with the session for logging
        - Executes the hook and handles errors
        - Always signals the before_lease_hook event to unblock connections

        Args:
            lease_scope: LeaseScope containing session, socket_path, and sync event
            report_status: Async callback to report status changes to controller
            shutdown: Callback to trigger exporter shutdown (accepts optional exit_code kwarg)
            request_lease_release: Async callback to request lease release from controller
        """
        should_release = False
        try:
            if not await self._wait_for_lease_ready(lease_scope, report_status):
                return

            # Check if hook is configured
            if not self.config.before_lease:
                logger.debug("No before-lease hook configured")
                await report_status(ExporterStatus.LEASE_READY, "Ready for commands")
                return

            # Skip hook if lease already ended (e.g. stale lease from backlog
            # when the exporter couldn't keep up with lease churn)
            if lease_scope.lease_ended.is_set():
                logger.info(
                    "Lease %s already ended, skipping beforeLease hook",
                    lease_scope.lease_name,
                )
                lease_scope.skip_after_lease_hook = True
                return

            await report_status(ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook")

            # Execute hook with lease scope
            logger.info("Executing before-lease hook for lease %s", lease_scope.lease_name)
            warning = await self._execute_hook(
                self.config.before_lease,
                lease_scope,
                LogSource.BEFORE_LEASE_HOOK,
            )

            if lease_scope.lease_ended.is_set():
                logger.info(
                    "Lease %s ended during beforeLease hook, skipping LEASE_READY transition",
                    lease_scope.lease_name,
                )
                return

            if warning:
                msg = f"{HOOK_WARNING_PREFIX}beforeLease hook warning: {warning}"
            else:
                msg = "Ready for commands"
            await report_status(ExporterStatus.LEASE_READY, msg)
            logger.info("beforeLease hook completed successfully")

        except HookExecutionError as e:
            if e.should_shutdown_exporter():
                # on_failure='exit' - defer shutdown until client handles the failure
                logger.error("beforeLease hook failed with on_failure='exit': %s", e)
                lease_scope.skip_after_lease_hook = True
                await report_status(
                    ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
                    f"beforeLease hook failed (on_failure=exit, shutting down): {e}",
                )
                await report_status(
                    ExporterStatus.OFFLINE,
                    "Exporter shutting down due to beforeLease hook failure",
                )
                # Defer shutdown: sets _stop_requested=True, actual stop after lease cleanup
                shutdown(exit_code=1, wait_for_lease_exit=True, should_unregister=True)
            else:
                # on_failure='endLease' - report failure, release in finally block
                logger.error("beforeLease hook failed with on_failure='endLease': %s", e)
                lease_scope.skip_after_lease_hook = True
                should_release = True
                await report_status(
                    ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
                    f"beforeLease hook failed (on_failure=endLease): {e}",
                )

        except Exception as e:  # noqa: BLE001
            logger.error("beforeLease hook failed with unexpected error: %s", e)
            await report_status(
                ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
                f"beforeLease hook failed: {e}",
            )
            # Unexpected errors don't trigger shutdown - just block the lease

        finally:
            # Always set the event to unblock connections
            lease_scope.before_lease_hook.set()

            # Release lease for endLease failure mode.
            # Shielded from cancellation to ensure the release completes
            # even if the task group is being torn down.
            if should_release:
                with CancelScope(shield=True):
                    await anyio.sleep(1.0)
                    await self._safe_release_lease(request_lease_release)

    async def run_after_lease_hook(
        self,
        lease_scope: "LeaseContext",
        report_status: Callable[["ExporterStatus", str], Awaitable[None]],
        shutdown: Callable[..., None],
        request_lease_release: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Execute after-lease hook with full orchestration.

        This method handles the complete lifecycle of running an after-lease hook:
        - Validates that the lease scope is ready
        - Reports status changes via the provided callback
        - Sets up the hook executor with the session for logging
        - Executes the hook and handles errors
        - Triggers shutdown on critical failures (HookExecutionError)
        - Requests lease release from controller after hook completes

        Args:
            lease_scope: LeaseScope containing session, socket_path, and client info
            report_status: Async callback to report status changes to controller
            shutdown: Callback to trigger exporter shutdown (accepts optional exit_code kwarg)
            request_lease_release: Async callback to request lease release from controller
        """
        shutdown_called = False
        try:
            # Verify lease scope is ready - for after-lease this should always be true
            # since we've already processed the lease, but check defensively
            if not lease_scope.is_ready():
                logger.warning("LeaseScope not ready for after-lease hook, skipping")
                await report_status(ExporterStatus.AVAILABLE, "Available for new lease")
                return

            # Check if hook is configured
            if not self.config.after_lease:
                logger.debug("No after-lease hook configured")
                await report_status(ExporterStatus.AVAILABLE, "Available for new lease")
                return

            await report_status(ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hooks")

            # Execute hook with lease scope
            logger.info("Executing after-lease hook for lease %s", lease_scope.lease_name)
            warning = await self._execute_hook(
                self.config.after_lease,
                lease_scope,
                LogSource.AFTER_LEASE_HOOK,
            )

            if warning:
                msg = f"{HOOK_WARNING_PREFIX}afterLease hook warning: {warning}"
            else:
                msg = "Available for new lease"
            await report_status(ExporterStatus.AVAILABLE, msg)
            logger.info("afterLease hook completed successfully")

        except HookExecutionError as e:
            if e.should_shutdown_exporter():
                # on_failure='exit' - shut down the entire exporter
                logger.exception("afterLease hook failed with on_failure='exit'")
                await report_status(
                    ExporterStatus.AFTER_LEASE_HOOK_FAILED,
                    f"afterLease hook failed (on_failure=exit, shutting down): {e}",
                )
                await report_status(
                    ExporterStatus.OFFLINE,
                    "Exporter shutting down due to afterLease hook failure",
                )
                # No delay needed - client is already polling and will see the failure
                logger.error("Shutting down exporter due to afterLease hook failure with on_failure='exit'")
                # Exit code 1 tells the CLI not to restart the exporter
                shutdown(exit_code=1, should_unregister=True, wait_for_lease_exit=True)
                shutdown_called = True
            else:
                # on_failure='endLease' - report failure to the client, then release the lease.
                # AFTER_LEASE_HOOK_FAILED is a transient status: the client sees the failure,
                # the lease is released in the finally block, and the exporter's main loop
                # clears the lease context and accepts new leases.
                logger.error("afterLease hook failed with on_failure='endLease': %s", e)
                await report_status(
                    ExporterStatus.AFTER_LEASE_HOOK_FAILED,
                    f"afterLease hook failed (on_failure=endLease): {e}",
                )

        except Exception as e:  # noqa: BLE001
            # Unexpected errors: report failure but do not shut down.
            # Same transient status - the lease is released and the exporter
            # accepts new leases after the finally block completes.
            logger.error("afterLease hook failed with unexpected error: %s", e)
            await report_status(
                ExporterStatus.AFTER_LEASE_HOOK_FAILED,
                f"afterLease hook failed: {e}",
            )

        finally:
            # Always delay to give client time to poll the final status
            await anyio.sleep(1.0)

            # Don't release lease when exporter is shutting down - unregistration handles cleanup.
            # Releasing here would report AVAILABLE to the controller right before shutdown.
            if request_lease_release and not shutdown_called:
                try:
                    await request_lease_release()
                except Exception:
                    logger.exception("Failed to request lease release")
