import os
import subprocess
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus
from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.exporter.hooks import (
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
    mock_session = MagicMock()
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
        """The WARNING log for on_failure='warn' must be emitted inside context_log_source
        so the warning is tagged with the hook source and visible to the client.
        """
        from contextlib import contextmanager

        from anyio import Event

        from jumpstarter.exporter.lease_context import LeaseContext

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="warn"),
        )
        executor = HookExecutor(config=hook_config)

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
            # Expected total: 0 + 1 + 4 + 9 == 14
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
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("Auto-detected Python script" in call for call in debug_calls)
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
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert not any("Auto-detected" in call for call in debug_calls)


    async def test_noninteractive_environment(self, lease_scope) -> None:
        """Test that hooks receive noninteractive environment variables.

        Verifies TERM=dumb, DEBIAN_FRONTEND=noninteractive, GIT_TERMINAL_PROMPT=0,
        and that PS1 is not set in the env dict passed to the subprocess.

        Note: PS1 is verified via _create_hook_env directly because shells
        may re-set PS1 from init files despite it being removed from the
        environment.
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
        # shells may re-set PS1 from profile/init files)
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

    async def test_before_lease_hook_endlease_sets_skip_flag_and_releases_lease(self, lease_scope) -> None:
        """Test that beforeLease hook failure with on_failure=endLease sets skip_after_lease_hook and releases lease."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="endLease"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()
        mock_request_lease_release = AsyncMock()

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_lease_release,
        )

        assert lease_scope.skip_after_lease_hook is True
        mock_request_lease_release.assert_called_once()
        mock_shutdown.assert_not_called()

    async def test_before_lease_hook_endlease_handles_release_error(self, lease_scope) -> None:
        """Test that beforeLease hook with on_failure=endLease handles release errors gracefully."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="exit 1", timeout=10, on_failure="endLease"),
        )
        executor = HookExecutor(config=hook_config)

        mock_report_status = AsyncMock()
        mock_shutdown = MagicMock()
        mock_request_lease_release = AsyncMock(side_effect=RuntimeError("controller unavailable"))

        # Should not raise even when request_lease_release fails
        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_lease_release,
        )

        assert lease_scope.skip_after_lease_hook is True
        mock_request_lease_release.assert_called_once()


    async def test_output_captured_without_trailing_newline(self, lease_scope) -> None:
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

    async def test_exec_default_is_none(self) -> None:
        """Test that the default exec is None (auto-detect)."""
        hook = HookInstanceConfigV1Alpha1(script="echo hello")
        assert hook.exec_ is None


class TestHookExecutorPRRegressions:
    """Regression tests for issues reported during PR review of hooks feature."""


    async def test_infrastructure_messages_at_debug_not_info(self, lease_scope) -> None:
        """Infrastructure messages must be at DEBUG, not INFO, so they
        don't appear in the client LogStream.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo 'user output'", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            await executor.execute_before_lease_hook(lease_scope)

            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            info_calls = [str(call) for call in mock_logger.info.call_args_list]

            infra_messages = [
                "Starting hook subprocess",
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

            assert any("user output" in call for call in info_calls)

    async def test_before_lease_hook_always_sets_event_on_failure(self, lease_scope) -> None:
        """before_lease_hook event must be set even when hook fails, to
        unblock process_connections in handle_lease.
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

        assert lease_scope.before_lease_hook.is_set()

    async def test_before_lease_hook_always_sets_event_on_exit(self, lease_scope) -> None:
        """before_lease_hook event must be set when hook fails with on_failure=exit."""
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
        """No hooks configured should transition directly to LEASE_READY."""
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

        assert any(
            status == ExporterStatus.LEASE_READY and msg == "Ready for commands"
            for status, msg in status_calls
        ), f"Expected LEASE_READY status, got: {status_calls}"

    async def test_skip_after_lease_prevents_after_hook_execution(self, lease_scope) -> None:
        """beforeLease fail+exit should prevent afterLease hook execution."""
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

        await executor.run_before_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert lease_scope.skip_after_lease_hook is True

        if not lease_scope.skip_after_lease_hook:
            await executor.run_after_lease_hook(
                lease_scope,
                mock_report_status,
                mock_shutdown,
            )

        after_hook_statuses = [s for s, _ in status_calls if s == ExporterStatus.AFTER_LEASE_HOOK]
        assert len(after_hook_statuses) == 0, (
            f"afterLease hook should have been skipped, but AFTER_LEASE_HOOK was reported: {status_calls}"
        )

    async def test_before_hook_exit_reports_failed_not_available(self, lease_scope) -> None:
        """beforeLease fail+exit should report FAILED, not AVAILABLE."""
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

        last_status, _ = status_calls[-1]
        assert last_status == ExporterStatus.OFFLINE, (
            f"Expected last status to be OFFLINE, got {last_status}"
        )

        failed_statuses = [s for s, _ in status_calls if s == ExporterStatus.BEFORE_LEASE_HOOK_FAILED]
        assert len(failed_statuses) > 0, (
            f"Expected BEFORE_LEASE_HOOK_FAILED status, got: {status_calls}"
        )

        available_statuses = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_statuses) == 0, (
            f"AVAILABLE should NOT be reported when beforeLease exits, got: {status_calls}"
        )

        mock_shutdown.assert_called_once_with(exit_code=1, wait_for_lease_exit=True, should_unregister=True)

    async def test_after_hook_exit_reports_failed_calls_shutdown(self, lease_scope) -> None:
        """afterLease fail+exit should report FAILED and call shutdown."""
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

        failed_statuses = [s for s, _ in status_calls if s == ExporterStatus.AFTER_LEASE_HOOK_FAILED]
        assert len(failed_statuses) > 0, (
            f"Expected AFTER_LEASE_HOOK_FAILED status, got: {status_calls}"
        )

        available_statuses = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_statuses) == 0, (
            f"AVAILABLE should NOT be reported when afterLease exits, got: {status_calls}"
        )

        mock_shutdown.assert_called_once_with(exit_code=1, should_unregister=True, wait_for_lease_exit=True)
        mock_request_release.assert_not_called()

    async def test_before_hook_warn_includes_warning_prefix(self, lease_scope) -> None:
        """beforeLease hook fail with warn should include HOOK_WARNING_PREFIX
        so shell.py can detect and display a user-visible warning.
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

        ready_calls = [(s, m) for s, m in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(ready_calls) == 1, f"Expected exactly one LEASE_READY, got: {status_calls}"
        _, msg = ready_calls[0]
        assert msg.startswith(HOOK_WARNING_PREFIX), (
            f"Expected LEASE_READY message to start with '{HOOK_WARNING_PREFIX}', got: '{msg}'"
        )

    async def test_before_hook_exit_reports_offline_before_shutdown(self, lease_scope) -> None:
        """OFFLINE must be reported before shutdown to prevent new lease assignment."""
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

        ready_calls = [s for s, _ in status_calls if s == ExporterStatus.LEASE_READY]
        assert len(ready_calls) == 1

        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
            mock_request_release,
        )

        available_calls = [s for s, _ in status_calls if s == ExporterStatus.AVAILABLE]
        assert len(available_calls) > 0, (
            f"Expected AVAILABLE status after warn+afterLease, got: {status_calls}"
        )

    async def test_after_lease_hook_skips_when_lease_context_not_ready(self) -> None:
        from anyio import Event

        from jumpstarter.exporter.lease_context import LeaseContext

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo should-not-run", timeout=10),
        )
        executor = HookExecutor(config=hook_config)

        lease_scope = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )

        status_calls: list[tuple] = []

        async def mock_report_status(status, msg):
            status_calls.append((status, msg))

        mock_shutdown = MagicMock()

        await executor.run_after_lease_hook(
            lease_scope,
            mock_report_status,
            mock_shutdown,
        )

        assert any(s == ExporterStatus.AVAILABLE for s, _ in status_calls)
        assert not any(s == ExporterStatus.AFTER_LEASE_HOOK for s, _ in status_calls)

    async def test_after_hook_warn_includes_warning_prefix(self, lease_scope) -> None:
        """afterLease hook fail with warn should include HOOK_WARNING_PREFIX."""
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


class TestPipeOutputEdgeCases:
    """Edge cases for pipe-based output capture (PR #837)."""

    async def test_stderr_captured_via_pipe_merge(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo STDOUT_LINE; echo STDERR_LINE >&2",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("STDOUT_LINE" in call for call in info_calls)
        assert any("STDERR_LINE" in call for call in info_calls)

    async def test_large_output_spanning_multiple_reads(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script=(
                    "seq 1 200 | while read n; do "
                    "echo \"LINE_${n}_PADDING_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\"; "
                    "done"
                ),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("LINE_1_" in call for call in info_calls)
        assert any("LINE_100_" in call for call in info_calls)
        assert any("LINE_200_" in call for call in info_calls)

    async def test_non_utf8_output_decoded_with_replacement(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script=(
                    "import sys; "
                    "sys.stdout.buffer.write(b'VALID_PREFIX\\x80VALID_SUFFIX\\n')"
                ),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        matching = [
            call for call in info_calls
            if "VALID_PREFIX" in call and "VALID_SUFFIX" in call
        ]
        assert len(matching) > 0

    async def test_rapid_exit_with_buffered_output(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo FAST_1; echo FAST_2; echo FAST_3; echo FAST_4; echo FAST_5",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        for i in range(1, 6):
            assert any(f"FAST_{i}" in call for call in info_calls), (
                f"FAST_{i} was not captured"
            )

    async def test_spawn_failure_cleans_up_without_crash(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="/nonexistent/interpreter",
                script="echo should not run",
                timeout=10,
                on_failure="warn",
            ),
        )
        executor = HookExecutor(config=hook_config)

        result = await executor.execute_before_lease_hook(lease_scope)
        assert result is not None
        assert "error" in result.lower()

    async def test_interleaved_stdout_and_stderr_captured(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script=(
                    "echo OUT_1; echo ERR_1 >&2; "
                    "echo OUT_2; echo ERR_2 >&2; "
                    "echo OUT_3"
                ),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        for label in ("OUT_1", "OUT_2", "OUT_3", "ERR_1", "ERR_2"):
            assert any(label in call for call in info_calls), (
                f"{label} was not captured"
            )

    async def test_timeout_with_grandchild_holding_pipe(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo GRANDCHILD_TEST; sleep 10 &",
                timeout=2,
                on_failure="warn",
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is not None
        assert "timed out" in result.lower()
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("GRANDCHILD_TEST" in call for call in info_calls)


    async def test_timeout_cleanup_handles_process_lookup_errors(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script=(
                    "import signal, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "time.sleep(300)\n"
                ),
                timeout=1,
                on_failure="warn",
            ),
        )
        executor = HookExecutor(config=hook_config)

        original_killpg = os.killpg

        def killpg_raises_after_real_signal(pgid, sig):
            try:
                original_killpg(pgid, sig)
            except ProcessLookupError:
                pass
            raise ProcessLookupError

        with patch("os.killpg", side_effect=killpg_raises_after_real_signal):
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is not None
        assert "timed out" in result.lower()

    async def test_exception_during_hook_triggers_finally_cleanup(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script=(
                    "import signal, time\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "time.sleep(300)\n"
                ),
                timeout=30,
                on_failure="warn",
            ),
        )
        executor = HookExecutor(config=hook_config)

        original_wait = subprocess.Popen.wait
        original_killpg = os.killpg
        wait_calls = [0]

        def failing_then_real_wait(self_popen, timeout=None):
            wait_calls[0] += 1
            if wait_calls[0] == 1:
                import time as _time

                _time.sleep(0.3)
                raise RuntimeError("simulated wait failure")
            return original_wait(self_popen, timeout=timeout)

        def killpg_raises_after_real_signal(pgid, sig):
            try:
                original_killpg(pgid, sig)
            except ProcessLookupError:
                pass
            raise ProcessLookupError

        with (
            patch.object(subprocess.Popen, "wait", failing_then_real_wait),
            patch("os.killpg", side_effect=killpg_raises_after_real_signal),
        ):
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is not None
        assert "error" in result.lower()


class TestReadOutputErrorPaths:
    """Tests for BlockingIOError and OSError handling in read_output.

    These exercise the read_output error paths via real subprocesses
    that produce controlled output patterns, and via _flush_lines
    for the partial buffer flush path.
    """

    async def test_blocking_io_error_path_via_nonblocking_pipe(self, lease_scope) -> None:
        """On a non-blocking pipe, reading before data arrives raises
        BlockingIOError. read_output handles this by continuing the loop.
        Verified via a script that delays output.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="sleep 0.1; echo DELAYED_OUTPUT",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("DELAYED_OUTPUT" in call for call in info_calls)

    async def test_reader_exits_on_eof(self, lease_scope) -> None:
        """read_output exits cleanly when os.read returns empty bytes (EOF)."""
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="echo BEFORE_CLOSE",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("BEFORE_CLOSE" in call for call in info_calls)

    def test_flush_lines_partial_buffer_preserved(self) -> None:
        """Partial buffer (no trailing newline) is returned for later flush."""
        output: list[str] = []
        remainder = _flush_lines(b"complete\npartial_data", output)
        assert output == ["complete"]
        assert remainder == b"partial_data"

    async def test_partial_buffer_flushed_on_exit(self, lease_scope) -> None:
        """When the subprocess exits with output lacking a trailing newline,
        the finally block in read_output flushes the partial buffer.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="printf 'NO_TRAILING_NEWLINE'",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("NO_TRAILING_NEWLINE" in call for call in info_calls)

    async def test_oserror_during_pipe_read_exits_gracefully(self, lease_scope) -> None:
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="python3",
                script=(
                    "import sys, time\n"
                    "sys.stdout.write('VISIBLE\\n')\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(0.5)\n"
                ),
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        original_wait_readable = anyio.wait_readable
        call_count = [0]

        async def wait_readable_then_oserror(fd):
            call_count[0] += 1
            if call_count[0] >= 3:
                raise OSError("simulated fd error")
            return await original_wait_readable(fd)

        with patch("anyio.wait_readable", side_effect=wait_readable_then_oserror):
            with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
                result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("VISIBLE" in call for call in info_calls)

    async def test_mixed_complete_and_partial_lines(self, lease_scope) -> None:
        """Complete lines are flushed immediately; the trailing partial
        is flushed when the subprocess exits.
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                script="printf 'LINE_A\\nLINE_B\\nPARTIAL_C'",
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)

        assert result is None
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("LINE_A" in call for call in info_calls)
        assert any("LINE_B" in call for call in info_calls)
        assert any("PARTIAL_C" in call for call in info_calls)
