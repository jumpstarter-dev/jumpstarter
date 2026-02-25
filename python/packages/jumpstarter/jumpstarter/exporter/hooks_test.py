from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus
from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.exporter.hooks import HookExecutionError, HookExecutor

pytestmark = pytest.mark.anyio


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

        Uses [[ ]] and bash array which would fail under /bin/sh on systems
        where sh is not bash (e.g. dash on Debian/Ubuntu).
        """
        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(
                exec_="/bin/bash",
                script='arr=(one two three); [[ ${#arr[@]} -eq 3 ]] && echo "BASH_OK: ${arr[1]}"',
                timeout=10,
            ),
        )
        executor = HookExecutor(config=hook_config)

        with patch("jumpstarter.exporter.hooks.logger") as mock_logger:
            result = await executor.execute_before_lease_hook(lease_scope)
            assert result is None
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("BASH_OK: two" in call for call in info_calls)

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

        # Last status should be BEFORE_LEASE_HOOK_FAILED
        last_status, _ = status_calls[-1]
        assert last_status == ExporterStatus.BEFORE_LEASE_HOOK_FAILED, (
            f"Expected last status to be BEFORE_LEASE_HOOK_FAILED, got {last_status}"
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
