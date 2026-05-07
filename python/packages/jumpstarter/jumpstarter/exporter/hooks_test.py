import os
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus
from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.exporter.hooks import (
    DRAIN_TIMEOUT_SECONDS,
    MAX_DRAIN_BYTES,
    HookExecutionError,
    HookExecutor,
    _flush_lines,
)

pytestmark = pytest.mark.anyio


class TestFlushLines:
    def test_extracts_complete_lines(self) -> None:
        output: list[str] = []
        remainder = _flush_lines(b"line1\nline2\npartial", output)
        assert output == ["line1", "line2"]
        assert remainder == b"partial"

    def test_returns_empty_when_all_consumed(self) -> None:
        output: list[str] = []
        remainder = _flush_lines(b"line1\nline2\n", output)
        assert output == ["line1", "line2"]
        assert remainder == b""

    def test_skips_empty_lines(self) -> None:
        output: list[str] = []
        remainder = _flush_lines(b"line1\n\nline2\n", output)
        assert output == ["line1", "line2"]
        assert remainder == b""

    def test_no_newlines_returns_buffer_unchanged(self) -> None:
        output: list[str] = []
        remainder = _flush_lines(b"no newline here", output)
        assert output == []
        assert remainder == b"no newline here"

    def test_empty_buffer(self) -> None:
        output: list[str] = []
        remainder = _flush_lines(b"", output)
        assert output == []
        assert remainder == b""


@pytest.fixture
def hook_config() -> HookConfigV1Alpha1:
    return HookConfigV1Alpha1(
        before_lease=HookInstanceConfigV1Alpha1(script="echo 'Pre-lease hook executed'", timeout=10),
        after_lease=HookInstanceConfigV1Alpha1(script="echo 'Post-lease hook executed'", timeout=10),
    )


@pytest.fixture
def lease_scope():
    from anyio import Event

    from jumpstarter.exporter.lease_context import LeaseContext

    lease_scope = LeaseContext(
        lease_name="test-lease-123",
        before_lease_hook=Event(),
        client_name="test-client",
    )
    # Add mock session to lease_scope
    mock_session = MagicMock()
    # Return a no-op context manager for context_log_source
    mock_session.context_log_source.return_value = nullcontext()
    lease_scope.session = mock_session
    lease_scope.socket_path = "/tmp/test_socket"
    return lease_scope


class TestHookExecutor:
    async def test_hook_executor_creation(self, hook_config) -> None:
        executor = HookExecutor(config=hook_config)

        assert executor.config == hook_config

    async def test_empty_hook_execution(self, lease_scope) -> None:
        empty_config = HookConfigV1Alpha1()
        executor = HookExecutor(config=empty_config)

        # Both hooks should return None for empty/None commands
        assert await executor.execute_before_lease_hook(lease_scope) is None
        assert await executor.execute_after_lease_hook(lease_scope) is None

    async def test_successful_hook_execution(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo 'Pre-lease hook executed'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)
        result = await executor.execute_before_lease_hook(lease_scope)
        assert result is None

    async def test_failed_hook_execution(self, lease_scope) -> None:
        failed_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="endLease"),
        )
        executor = HookExecutor(config=failed_config)

        with pytest.raises(HookExecutionError) as exc_info:
            await executor.execute_before_lease_hook(lease_scope)

        assert "exit code 1" in str(exc_info.value)
        assert exc_info.value.on_failure == "endLease"
        assert exc_info.value.hook_type == "before_lease"

    async def test_hook_timeout(self, lease_scope) -> None:
        timeout_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="sleep 60", timeout=1, on_failure="exit"),
        )
        executor = HookExecutor(config=timeout_config)

        with pytest.raises(HookExecutionError) as exc_info:
            await executor.execute_before_lease_hook(lease_scope)

        assert "timed out after 1 seconds" in str(exc_info.value)
        assert exc_info.value.on_failure == "exit"

    async def test_hook_environment_variables(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo LEASE_NAME=$LEASE_NAME; echo CLIENT_NAME=$CLIENT_NAME", timeout=10
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            await executor.execute_before_lease_hook(lease_scope)
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("LEASE_NAME=test-lease-123" in call for call in info_calls)
            assert any("CLIENT_NAME=test-client" in call for call in info_calls)

    async def test_real_time_output_logging(self, lease_scope) -> None:
        """Test that hook output is logged in real-time at INFO level."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo 'Line 1'; echo 'Line 2'; echo 'Line 3'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

            assert result is None

            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Line 1" in call for call in info_calls)
            assert any("Line 2" in call for call in info_calls)
            assert any("Line 3" in call for call in info_calls)

    async def test_post_lease_hook_execution_on_completion(self, lease_scope) -> None:
        """Test that post-lease hook executes when called directly."""
        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo 'Post-lease cleanup completed'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_after_lease_hook(lease_scope)

            assert result is None

            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Post-lease cleanup completed" in call for call in info_calls)

    async def test_hook_timeout_with_warn(self, lease_scope) -> None:
        """Test that hook returns warning string when timeout occurs and on_failure='warn'."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="sleep 60", timeout=1, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is not None
            assert "timed out" in result.lower()
            # Verify WARNING log was created
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("on_failure=warn, continuing" in call for call in warning_calls)

    async def test_failed_hook_with_warn_returns_warning(self, lease_scope) -> None:
        """Test that hook with exit 1 and on_failure='warn' returns a warning string."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        result = await executor.execute_before_lease_hook(lease_scope)
        assert result is not None
        assert "exit code 1" in result.lower()

    async def test_failed_hook_with_warn_logs_warning_inside_log_source_context(self) -> None:
        """Test that the WARNING log for on_failure='warn' is emitted inside context_log_source.

        Issue #246: The WARNING log from _handle_hook_failure must be emitted while
        the context_log_source context manager is active. This ensures the warning
        is tagged with the hook source (BEFORE_LEASE_HOOK / AFTER_LEASE_HOOK) and
        is visible to the client even without --exporter-logs.
        """
        from contextlib import contextmanager

        from anyio import Event

        from jumpstarter.exporter.lease_context import LeaseContext

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        # Track whether context_log_source is active when warning is logged
        context_active = False
        warning_logged_in_context = False

        @contextmanager
        def tracking_context_log_source(logger_name, source):
            nonlocal context_active
            context_active = True
            try:
                yield
            finally:
                context_active = False

        lease_scope = LeaseContext(
            lease_name="test-lease-ctx",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        mock_session = MagicMock()
        mock_session.context_log_source.side_effect = tracking_context_log_source
        lease_scope.session = mock_session
        lease_scope.socket_path = "/tmp/test_socket"

        original_handle = executor._handle_hook_failure

        def tracking_handle(error_msg, on_failure, hook_type, cause=None):
            nonlocal warning_logged_in_context
            warning_logged_in_context = context_active
            return original_handle(error_msg, on_failure, hook_type, cause)

        executor._handle_hook_failure = tracking_handle

        result = await executor.execute_before_lease_hook(lease_scope)
        assert result is not None
        assert "exit code 1" in result.lower()
        assert warning_logged_in_context, (
            "WARNING log from _handle_hook_failure must be emitted inside context_log_source "
            "so it is visible to the client as a hook log (issue #246)"
        )

    async def test_successful_hook_returns_none(self, lease_scope) -> None:
        """Test that a successful hook returns None (no warning)."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo 'hello'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        result = await executor.execute_before_lease_hook(lease_scope)
        assert result is None

    async def test_exec_bash(self, lease_scope) -> None:
        """Test that exec=/bin/bash allows bash-specific syntax.

        Uses ${var:offset:length} substring syntax which is bash-specific
        and would fail under /bin/sh on systems where sh is dash.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="/bin/bash",
                script='V="hello_world"; echo "BASH_OK: ${V:6:5}"',
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("BASH_OK: world" in call for call in info_calls)

    async def test_exec_python3(self, lease_scope) -> None:
        """Test that exec=python3 runs inline Python.

        Uses Python-only syntax (list comprehension, f-string) that would
        fail if run as a shell script.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script="result = sum([x*x for x in range(4)])\nprint(f'PYTHON_OK: {result}')",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            # sum([0, 1, 4, 9]) == 14
            assert any("PYTHON_OK: 14" in call for call in info_calls)

    async def test_script_file_sh(self, lease_scope, tmp_path) -> None:
        """Test that a .sh file auto-detects /bin/sh as interpreter."""
        script_file = tmp_path / "hook_script.sh"
        script_file.write_text("#!/bin/sh\necho 'SHFILE_OK'\n")
        script_file.chmod(0o755)

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script=str(script_file),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("SHFILE_OK" in call for call in info_calls)
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("Executing script file" in call for call in debug_calls)

    async def test_script_file_py_autodetects_python(self, lease_scope, tmp_path) -> None:
        """Test that a .py file auto-detects the exporter's Python as interpreter."""
        import sys

        script_file = tmp_path / "hook_script.py"
        script_file.write_text("import sys\nprint(f'PYFILE_OK: {sys.executable}')\n")

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script=str(script_file),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("PYFILE_OK" in call for call in info_calls)
            # Verify it auto-detected Python (now logged at DEBUG level)
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("Auto-detected Python script" in call for call in debug_calls)
            # Verify it used the exporter's own Python interpreter
            assert any(sys.executable in call for call in debug_calls)

    async def test_script_file_py_exec_override(self, lease_scope, tmp_path) -> None:
        """Test that explicit exec overrides .py auto-detection."""
        script_file = tmp_path / "hook_script.py"
        script_file.write_text("print('OVERRIDE_OK')\n")

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script=str(script_file),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("OVERRIDE_OK" in call for call in info_calls)
            # Should NOT say "Auto-detected" since exec was explicitly set
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert not any("Auto-detected" in call for call in debug_calls)

    async def test_noninteractive_environment(self, lease_scope) -> None:
        """Test that hooks receive noninteractive environment variables.

        Verifies TERM=dumb, DEBIAN_FRONTEND=noninteractive, GIT_TERMINAL_PROMPT=0,
        and that PS1 is not set in the env dict passed to the subprocess.

        Note: PS1 is verified via _create_hook_env directly because shells
        started in a PTY may re-set PS1 from init files despite it being
        removed from the environment.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script=(
                    'echo "TERM=$TERM";'
                    ' echo "DEBIAN_FRONTEND=$DEBIAN_FRONTEND";'
                    ' echo "GIT_TERMINAL_PROMPT=$GIT_TERMINAL_PROMPT"'
                ),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        # Verify PS1 is removed from the env dict (not via subprocess, since
        # shells in a PTY may re-set PS1 from profile/init files)
        hook_env = executor._create_hook_env(lease_scope)
        assert "PS1" not in hook_env

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            await executor.execute_before_lease_hook(lease_scope)
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("TERM=dumb" in call for call in info_calls)
            assert any("DEBIAN_FRONTEND=noninteractive" in call for call in info_calls)
            assert any("GIT_TERMINAL_PROMPT=0" in call for call in info_calls)

    async def test_before_lease_hook_exit_sets_skip_flag(self, lease_scope) -> None:
        """Test that beforeLease hook failure with on_failure=exit sets skip_after_lease_hook flag."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()

        assert lease_scope.skip_after_lease_hook is False

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.skip_after_lease_hook is True
        mock_shutdown.assert_called_once_with(exit_code=1, wait_for_lease_exit=True, should_unregister=True)

    async def test_before_lease_hook_endlease_does_not_set_skip_flag(self, lease_scope) -> None:
        """Test that beforeLease hook failure with on_failure=endLease does NOT set skip_after_lease_hook."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="endLease"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.skip_after_lease_hook is False

    async def test_pty_output_drained_after_stop_flag_set(self) -> None:
        """Test that PTY drain captures data remaining after the stop flag is set.

        Simulates the macOS scenario where PTY output is still in the kernel
        buffer after the subprocess exits and reader_stop is set. Uses a pipe
        to inject data, sets reader_stop=True to skip the main loop, and
        verifies the finally-block drain captures all lines.
        """
        import fcntl
        import time

        read_fd, write_fd = os.pipe()
        try:
            flags = fcntl.fcntl(read_fd, fcntl.F_GETFL)
            fcntl.fcntl(read_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            os.write(write_fd, b"DRAIN_LINE_1\nDRAIN_LINE_2\nDRAIN_LINE_3\n")
            os.close(write_fd)
            write_fd = -1

            output_lines: list[str] = []
            buffer = b""

            drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
            drained = 0
            while drained < MAX_DRAIN_BYTES and time.monotonic() < drain_deadline:
                try:
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        break
                    buffer += chunk
                    drained += len(chunk)
                except (BlockingIOError, OSError):
                    break

            buffer = _flush_lines(buffer, output_lines)

            assert "DRAIN_LINE_1" in output_lines
            assert "DRAIN_LINE_2" in output_lines
            assert "DRAIN_LINE_3" in output_lines
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)

    async def test_drain_respects_byte_limit(self) -> None:
        """Verify the drain loop stops after MAX_DRAIN_BYTES to prevent
        indefinite blocking when a grandchild process holds the PTY open.

        Directly tests the drain logic using a pipe with data exceeding the
        byte limit. Uses non-blocking writes to fill the pipe without blocking.
        """
        import fcntl
        import time

        read_fd, write_fd = os.pipe()
        try:
            flags = fcntl.fcntl(read_fd, fcntl.F_GETFL)
            fcntl.fcntl(read_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            wflags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, wflags | os.O_NONBLOCK)

            total_written = 0
            chunk = b"X" * 4000 + b"\n"
            try:
                while True:
                    os.write(write_fd, chunk)
                    total_written += len(chunk)
            except BlockingIOError:
                pass

            assert total_written > 0

            output_lines: list[str] = []
            buffer = b""
            drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
            drained = 0
            while drained < MAX_DRAIN_BYTES and time.monotonic() < drain_deadline:
                try:
                    data = os.read(read_fd, 4096)
                    if not data:
                        break
                    buffer += data
                    drained += len(data)
                except (BlockingIOError, OSError):
                    break

            buffer = _flush_lines(buffer, output_lines)

            assert drained <= MAX_DRAIN_BYTES
            assert len(output_lines) > 0
        finally:
            os.close(read_fd)
            os.close(write_fd)

    async def test_drain_completes_immediately_on_empty_buffer(self) -> None:
        """Verify drain exits quickly when the PTY buffer is empty (EOF)."""
        import time

        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            import fcntl

            flags = fcntl.fcntl(read_fd, fcntl.F_GETFL)
            fcntl.fcntl(read_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            output_lines: list[str] = []
            buffer = b""
            start = time.monotonic()

            drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
            drained = 0
            while drained < MAX_DRAIN_BYTES and time.monotonic() < drain_deadline:
                try:
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        break
                    buffer += chunk
                    drained += len(chunk)
                except (BlockingIOError, OSError):
                    break

            buffer = _flush_lines(buffer, output_lines)
            elapsed = time.monotonic() - start

            assert output_lines == []
            assert drained == 0
            assert elapsed < 0.5
        finally:
            os.close(read_fd)

    async def test_drain_handles_oserror_gracefully(self) -> None:
        """Verify drain exits gracefully when os.read raises OSError (e.g. EIO)."""
        import time

        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        os.close(read_fd)

        output_lines: list[str] = []
        buffer = b""

        drain_deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        drained = 0
        while drained < MAX_DRAIN_BYTES and time.monotonic() < drain_deadline:
            try:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                buffer += chunk
                drained += len(chunk)
            except (BlockingIOError, OSError):
                break

        buffer = _flush_lines(buffer, output_lines)

        assert output_lines == []
        assert drained == 0

    async def test_drain_captures_output_without_trailing_newline(self, lease_scope) -> None:
        """Verify output without a trailing newline is still captured."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="printf 'NO_NEWLINE_OUTPUT'",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("NO_NEWLINE_OUTPUT" in call for call in info_calls)

    async def test_drain_reads_data_remaining_in_pty_buffer(self, lease_scope) -> None:
        """Verify the drain loop inside read_pty_output reads data left in the
        PTY kernel buffer after the main read loop exits.

        Patches os.read so that, once the main loop has consumed the initial
        subprocess output via EOF from the specific PTY fd, a subsequent read
        returns additional data -- simulating the macOS scenario where the
        kernel buffers output that arrives after the reader stop flag is set.
        """
        import pty

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo MAIN_OUTPUT",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        original_os_read = os.read
        original_openpty = pty.openpty
        pty_parent_fd = None
        eof_seen_on_pty = False

        def tracking_openpty():
            nonlocal pty_parent_fd
            parent, child = original_openpty()
            pty_parent_fd = parent
            return parent, child

        drain_data_returned = False

        def os_read_with_drain_data(fd, size):
            nonlocal eof_seen_on_pty, drain_data_returned
            if fd != pty_parent_fd:
                return original_os_read(fd, size)
            if not eof_seen_on_pty:
                try:
                    data = original_os_read(fd, size)
                except (BlockingIOError, OSError):
                    if not eof_seen_on_pty:
                        eof_seen_on_pty = True
                    raise
                if not data:
                    eof_seen_on_pty = True
                    return b""
                return data
            if not drain_data_returned:
                drain_data_returned = True
                return b"DRAIN_CAPTURED\n"
            return b""

        with (
            patch("pty.openpty", side_effect=tracking_openpty),
            patch("os.read", side_effect=os_read_with_drain_data),
            patch("jumpstarter.exporter.hooks.logger") as mock_logger,
        ):
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            assert pty_parent_fd is not None
            assert eof_seen_on_pty
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("DRAIN_CAPTURED" in call for call in info_calls)

    async def test_drain_exception_is_suppressed(self, lease_scope) -> None:
        """Verify that an unexpected exception raised during the drain is caught
        by the except-Exception handler and does not propagate to the caller.

        Patches _flush_lines so that the second call (inside the drain) raises
        a RuntimeError. The hook should still complete successfully because the
        drain's except-Exception block suppresses it.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo BEFORE_DRAIN_ERROR",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        original_flush = _flush_lines
        call_count = 0

        def flush_lines_with_drain_error(buffer, output_lines):
            nonlocal call_count
            call_count += 1
            result = original_flush(buffer, output_lines)
            if call_count > 1:
                raise RuntimeError("simulated drain error")
            return result

        with (
            patch("jumpstarter.exporter.hooks._flush_lines", side_effect=flush_lines_with_drain_error),
            patch("jumpstarter.exporter.hooks.logger"),
        ):
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None

    async def test_drain_constants_are_reasonable(self) -> None:
        assert MAX_DRAIN_BYTES == 256 * 1024
        assert DRAIN_TIMEOUT_SECONDS == 2.0

    async def test_exec_default_is_none(self) -> None:
        """Test that the default exec is None (auto-detect)."""
        hook = HookInstanceConfigV1Alpha1(script="echo hello")
        assert hook.exec_ is None


class TestHookExecutorPRRegressions:
    """Regression tests for issues reported during PR review of hooks feature."""

    async def test_infrastructure_messages_at_debug_not_info(self, lease_scope) -> None:
        """Issue A1: Hook infrastructure messages should be at DEBUG, not INFO.

        Infrastructure messages like 'Starting hook subprocess', 'Creating PTY',
        'Spawning subprocess', 'Subprocess spawned', 'Subprocess completed', and
        'Hook executed successfully' must be logged at DEBUG level so they don't
        appear in the client LogStream at the default INFO level. Only user output
        from the hook script should be at INFO.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo 'user output'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            await executor.execute_before_lease_hook(lease_scope)

            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            info_calls = [str(call) for call in mock_logger.info.call_args_list]

            # Infrastructure messages should be at DEBUG level
            infra_messages = [
                "Starting hook subprocess",
                "Creating PTY",
                "Spawning subprocess",
                "Subprocess spawned",
                "Hook executed successfully",
            ]
            for msg in infra_messages:
                assert any(msg in call for call in debug_calls), (
                    f"Expected '{msg}' at DEBUG level, not found in debug calls"
                )
                assert not any(msg in call for call in info_calls), (
                    f"Infrastructure message '{msg}' should NOT be at INFO level"
                )

            # User output should be at INFO level
            assert any("user output" in call for call in info_calls)

    async def test_before_lease_hook_always_sets_event_on_failure(self, lease_scope) -> None:
        """Issue C3: before_lease_hook event must be set even when hook fails.

        When the beforeLease hook fails with on_failure=endLease, the event must
        still be set to unblock process_connections in handle_lease. Otherwise
        the lease hangs indefinitely.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="endLease"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()

        assert not lease_scope.before_lease_hook.is_set()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # Event must always be set to unblock connections
        assert lease_scope.before_lease_hook.is_set()

    async def test_before_lease_hook_always_sets_event_on_exit(self, lease_scope) -> None:
        """Issue C3b: before_lease_hook event must be set when hook fails with exit.

        Same as C3 but for on_failure=exit. The event must be set, shutdown called,
        and skip_after_lease_hook set to True.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.before_lease_hook.is_set()
        assert lease_scope.skip_after_lease_hook is True
        mock_shutdown.assert_called_once()

    async def test_no_hooks_transitions_to_lease_ready(self, lease_scope) -> None:
        """Issue D1: No hooks configured should transition directly to LEASE_READY.

        When no hooks are configured, run_before_lease_hook should report
        LEASE_READY immediately, preventing the 'create lease, never use → stuck'
        scenario.
        """
        empty_config = HookConfigV1Alpha1()
        executor = HookExecutor(config=empty_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # Should have reported LEASE_READY
        assert any(
            status == ExporterStatus.LEASE_READY and msg == "Ready for commands"
            for status, msg in status_calls
        ), f"Expected LEASE_READY status, got: {status_calls}"

    async def test_skip_after_lease_prevents_after_hook_execution(self, lease_scope) -> None:
        """Issue E1: beforeLease fail+exit should prevent afterLease hook execution.

        When beforeLease fails with on_failure=exit, skip_after_lease_hook is set
        to True. The handle_lease finally block checks this flag and skips the
        afterLease hook. This test verifies the orchestration sequence.
        """
        # Config with both hooks
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
            after_lease=HookInstanceConfigV1Alpha1(script="echo 'SHOULD NOT RUN'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        # Run before hook (which fails and sets skip flag)
        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.skip_after_lease_hook is True

        # Now simulate what handle_lease does: check the flag before running after hook
        # This mirrors the actual code: `if not lease_scope.skip_after_lease_hook:`
        if not lease_scope.skip_after_lease_hook:
            await executor.run_after_lease_hook(
                lease_scope,
                mock_report_status,
                mock_shutdown,
            )

        # AFTER_LEASE_HOOK status should never have been reported
        after_hook_statuses = [s for s, _ in status_calls if s == ExporterStatus.AFTER_LEASE_HOOK]
        assert len(after_hook_statuses) == 0, (
            f"afterLease hook should have been skipped, but AFTER_LEASE_HOOK was reported: {status_calls}"
        )

    async def test_before_hook_exit_reports_failed_not_available(self, lease_scope) -> None:
        """Issue E2: beforeLease fail+exit should report FAILED, not AVAILABLE.

        When beforeLease hook fails with on_failure=exit, the last status must be
        BEFORE_LEASE_HOOK_FAILED. It should NOT report AVAILABLE, which would
        incorrectly tell the controller the exporter is ready for new leases.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # Last status should be OFFLINE (reported before shutdown to prevent new leases)
        last_status, _ = status_calls[-1]
        assert last_status == ExporterStatus.OFFLINE, (
            f"Expected last status to be OFFLINE, got {last_status}"
        )

        # BEFORE_LEASE_HOOK_FAILED should also be present (reported before OFFLINE)
        failed_statuses = [s for s, _ in status_calls if s == ExporterStatus.BEFORE_LEASE_HOOK_FAILED]
        assert len(failed_statuses) > 0, (
            f"Expected BEFORE_LEASE_HOOK_FAILED status, got: {status_calls}"
        )

        # AVAILABLE should never have been reported
        available_statuses = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_statuses) == 0, (
            f"AVAILABLE should NOT be reported when beforeLease exits, got: {status_calls}"
        )

        # Shutdown should have been called with correct args
        mock_shutdown.assert_called_once_with(exit_code=1, wait_for_lease_exit=True, should_unregister=True)

    async def test_after_hook_exit_reports_failed_calls_shutdown(self, lease_scope) -> None:
        """Issue E3: afterLease fail+exit should report FAILED and call shutdown.

        When afterLease hook fails with on_failure=exit:
        - AFTER_LEASE_HOOK_FAILED status must be reported
        - AVAILABLE must NOT be reported
        - shutdown must be called (not request_lease_release)
        """
        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()
        mock_request_release = AsyncMock()

        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_release,
        )

        # AFTER_LEASE_HOOK_FAILED should be in statuses
        failed_statuses = [s for s, _ in status_calls if s == ExporterStatus.AFTER_LEASE_HOOK_FAILED]
        assert len(failed_statuses) > 0, (
            f"Expected AFTER_LEASE_HOOK_FAILED status, got: {status_calls}"
        )

        # AVAILABLE should NOT be in statuses
        available_statuses = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_statuses) == 0, (
            f"AVAILABLE should NOT be reported when afterLease exits, got: {status_calls}"
        )

        # Shutdown called (not request_lease_release)
        mock_shutdown.assert_called_once_with(exit_code=1, should_unregister=True, wait_for_lease_exit=True)
        mock_request_release.assert_not_called()

    async def test_before_hook_warn_includes_warning_prefix(self, lease_scope) -> None:
        """Issue E5: beforeLease hook fail with warn should include HOOK_WARNING_PREFIX.

        The status message for LEASE_READY must start with '[HOOK_WARNING] ' so that
        shell.py can detect it and display a user-visible warning.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # Find the LEASE_READY status call
        ready_calls = [(s, m) for s, m in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(ready_calls) == 1, f"Expected exactly one LEASE_READY, got: {status_calls}"
        _, msg = ready_calls[0]
        assert msg.startswith(HOOK_WARNING_PREFIX), (
            f"Expected LEASE_READY message to start with '{HOOK_WARNING_PREFIX}', got: '{msg}'"
        )

    async def test_before_hook_exit_reports_offline_before_shutdown(self, lease_scope) -> None:
        """When beforeLease hook fails with on_failure=exit, the exporter must
        report OFFLINE status to the controller before initiating shutdown.
        This prevents the controller from assigning new leases to a dying
        exporter during the shutdown window.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []
        shutdown_called_at_index = None

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        def mock_shutdown(**kwargs):
            nonlocal shutdown_called_at_index
            shutdown_called_at_index = len(status_calls)

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        offline_indices = [
            i for i, (s, _) in enumerate(status_calls) if s == ExporterStatus.OFFLINE
        ]
        assert len(offline_indices) > 0, (
            f"Expected OFFLINE status before shutdown, got: {status_calls}"
        )
        assert shutdown_called_at_index is not None, "shutdown was never called"
        assert offline_indices[0] < shutdown_called_at_index, (
            f"OFFLINE (index {offline_indices[0]}) must be reported before "
            f"shutdown (index {shutdown_called_at_index}). Statuses: {status_calls}"
        )

    async def test_after_hook_exit_reports_offline_before_shutdown(self, lease_scope) -> None:
        """When afterLease hook fails with on_failure=exit, OFFLINE must be
        reported before shutdown to prevent new lease assignment."""
        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="exit"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []
        shutdown_called_at_index = None

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        def mock_shutdown(**kwargs):
            nonlocal shutdown_called_at_index
            shutdown_called_at_index = len(status_calls)

        mock_request_release = AsyncMock()

        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_release,
        )

        offline_indices = [
            i for i, (s, _) in enumerate(status_calls) if s == ExporterStatus.OFFLINE
        ]
        assert len(offline_indices) > 0, (
            f"Expected OFFLINE status before shutdown, got: {status_calls}"
        )
        assert shutdown_called_at_index is not None, "shutdown was never called"
        assert offline_indices[0] < shutdown_called_at_index, (
            f"OFFLINE (index {offline_indices[0]}) must be reported before "
            f"shutdown (index {shutdown_called_at_index}). Statuses: {status_calls}"
        )

    async def test_warn_failure_during_premature_lease_end_still_transitions_available(self, lease_scope) -> None:
        """Edge case: onFailure:warn during premature lease-end.

        When a beforeLease hook fails with on_failure=warn during a premature
        lease-end, the warning is logged but afterLease cleanup still proceeds
        and the exporter transitions to AVAILABLE.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()
        mock_request_release = AsyncMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # beforeLease with warn should still transition to LEASE_READY
        ready_calls = [s for s, _ in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(ready_calls) == 1

        # Now run afterLease (simulating premature lease-end cleanup)
        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_release,
        )

        # afterLease hook should run and transition to AVAILABLE
        available_calls = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_calls) > 0, (
            f"Expected AVAILABLE status after warn+afterLease, got: {status_calls}"
        )

    async def test_after_hook_warn_includes_warning_prefix(self, lease_scope) -> None:
        """Issue E5b: afterLease hook fail with warn should include HOOK_WARNING_PREFIX.

        The status message for AVAILABLE must start with '[HOOK_WARNING] ' so that
        shell.py can detect it and display a user-visible warning after session ends.
        """
        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        # Find the AVAILABLE status call
        available_calls = [(s, m) for s, m in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_calls) == 1, f"Expected exactly one AVAILABLE, got: {status_calls}"
        _, msg = available_calls[0]
        assert msg.startswith(HOOK_WARNING_PREFIX), (
            f"Expected AVAILABLE message to start with '{HOOK_WARNING_PREFIX}', got: '{msg}'"
        )


class TestBeforeLeaseHookLeaseEndedGuard:
    """Tests for the race condition where beforeLease hook completes after
    the lease has already expired. When lease_ended is set, the hook must
    NOT set status to LEASE_READY, preventing the exporter from being
    stuck in LEASE_READY permanently."""

    async def test_run_before_lease_hook_skips_lease_ready_when_lease_ended(self, lease_scope) -> None:
        """When the lease has already ended by the time the beforeLease hook
        completes, status must NOT be set to LEASE_READY."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        lease_scope.lease_ended.set()

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        lease_ready_calls = [s for s, _ in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(lease_ready_calls) == 0, (
            f"LEASE_READY must NOT be set when lease has already ended, got: {status_calls}"
        )

        hook_started_calls = [s for s, _ in status_calls if s == ExporterStatus.BEFORE_LEASE_HOOK]
        assert len(hook_started_calls) == 1, (
            f"BEFORE_LEASE_HOOK must be reported (hook must run) even when lease has ended, got: {status_calls}"
        )

    async def test_run_before_lease_hook_sets_event_even_when_lease_ended(self, lease_scope) -> None:
        """The before_lease_hook event must always be set (via the finally block)
        even when the lease has ended, to unblock downstream waiters."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        lease_scope.lease_ended.set()

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.before_lease_hook.is_set(), (
            "before_lease_hook event must be set even when lease has ended"
        )

    async def test_run_before_lease_hook_warn_skips_lease_ready_when_lease_ended(self, lease_scope) -> None:
        """When hook fails with on_failure=warn and the lease has already ended,
        LEASE_READY must still be skipped."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

        lease_scope.lease_ended.set()

        status_calls = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        lease_ready_calls = [s for s, _ in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(lease_ready_calls) == 0, (
            f"LEASE_READY must NOT be set when lease has ended (even with warn), got: {status_calls}"
        )

        hook_started_calls = [s for s, _ in status_calls if s == ExporterStatus.BEFORE_LEASE_HOOK]
        assert len(hook_started_calls) == 1, (
            f"BEFORE_LEASE_HOOK must be reported (hook must run) even when lease has ended, got: {status_calls}"
        )
