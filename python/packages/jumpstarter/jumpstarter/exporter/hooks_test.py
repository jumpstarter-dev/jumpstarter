from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

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
            assert any("Executing script file" in call for call in info_calls)

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
            # Verify it auto-detected Python
            assert any("Auto-detected Python script" in call for call in info_calls)
            # Verify it used the exporter's own Python interpreter
            assert any(sys.executable in call for call in info_calls)

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
            assert not any("Auto-detected" in call for call in info_calls)

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

    async def test_exec_default_is_none(self) -> None:
        """Test that the default exec is None (auto-detect)."""
        hook = HookInstanceConfigV1Alpha1(script="echo hello")
        assert hook.exec_ is None
