"""Tests for the exporter run module, covering rapid failure detection and CLI argument validation."""

from unittest.mock import MagicMock, patch

import click
import click.testing
import pytest

import jumpstarter_cli.run as run_mod
from jumpstarter_cli.run import _validate_standalone_auth, run


def _make_config(max_rapid_failures=5, rapid_failure_window=60):
    """Create a mock config with failure_detection settings."""
    config = MagicMock()
    config.failure_detection.max_rapid_failures = max_rapid_failures
    config.failure_detection.rapid_failure_window = rapid_failure_window
    return config


class TestServeWithExcHandlingRapidFailures:
    """Test rapid failure detection in _serve_with_exc_handling.

    These tests mock os.fork and _handle_parent/_handle_child to simulate
    the restart loop without actually forking processes.
    """

    def test_exits_after_max_rapid_failures(self):
        """When children fail rapidly max_rapid_failures times, the function should exit with code 1."""
        counter = [0]
        config = _make_config()
        max_failures = config.failure_detection.max_rapid_failures

        def mock_fork():
            counter[0] += 1
            return counter[0]  # Always return positive (parent path)

        def mock_handle_parent(pid):
            return None  # Simulate child exit code 0 -> restart

        # Simulate time.monotonic returning values that make each child
        # appear to fail within the rapid failure window.
        time_values = []
        for i in range(max_failures + 1):
            time_values.append(float(i * 2))       # child_start_time
            time_values.append(float(i * 2 + 1))   # elapsed check (1s later, < 60s window)
        time_iter = iter(time_values)

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 1
        assert counter[0] == max_failures

    def test_resets_counter_after_long_running_child(self):
        """A child that runs longer than rapid_failure_window resets the rapid failure counter."""
        config = _make_config()
        max_failures = config.failure_detection.max_rapid_failures
        counter = [0]
        rapid_before_reset = max_failures - 1

        def mock_fork():
            counter[0] += 1
            return counter[0]

        def mock_handle_parent(pid):
            return None  # Always restart

        # Build time values:
        # First (MAX-1) calls fail rapidly, then one runs long (resets counter),
        # then MAX more fail rapidly -> triggers exit.
        time_values = []

        for i in range(rapid_before_reset):
            time_values.append(float(i * 2))       # start
            time_values.append(float(i * 2 + 1))   # elapsed = 1s (rapid)

        # Long running child (resets counter)
        base = rapid_before_reset * 2
        time_values.append(float(base))             # start
        time_values.append(float(base + 100))       # elapsed = 100s (not rapid)

        # More rapid failures after reset - need exactly MAX to trigger exit
        base2 = base + 100
        for i in range(max_failures):
            time_values.append(float(base2 + i * 2))       # start
            time_values.append(float(base2 + i * 2 + 1))   # elapsed = 1s (rapid)

        time_iter = iter(time_values)

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        # The function should exit due to rapid failures AFTER the reset
        # Total calls: (MAX-1) rapid + 1 long + MAX rapid
        assert exit_code == 1
        expected_calls = rapid_before_reset + 1 + max_failures
        assert counter[0] == expected_calls

    def test_normal_exit_code_passed_through(self):
        """When _handle_parent returns a non-None exit code, it should be passed through."""

        def mock_fork():
            return 1

        def mock_handle_parent(pid):
            return 137  # Simulating killed by signal

        config = _make_config()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", return_value=0.0),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 137

    def test_single_rapid_failure_does_not_exit(self):
        """A single rapid failure should not cause the main process to exit."""
        counter = [0]

        def mock_fork():
            counter[0] += 1
            return counter[0]

        def mock_handle_parent(pid):
            if counter[0] == 1:
                return None  # First child exits with 0 (rapid failure)
            return 0  # Second child exits normally with explicit code

        # First child: rapid failure (1s). Second child: normal exit code returned.
        time_values = [0.0, 1.0, 2.0, 3.0]
        time_iter = iter(time_values)
        config = _make_config()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        # Should exit with 0 from the second child, not 1 from rapid failure detection
        assert exit_code == 0
        assert counter[0] == 2

    def test_custom_config_values(self):
        """Verify that custom max_rapid_failures and rapid_failure_window are respected."""
        counter = [0]
        config = _make_config(max_rapid_failures=3, rapid_failure_window=10)

        def mock_fork():
            counter[0] += 1
            return counter[0]

        def mock_handle_parent(pid):
            return None  # Always restart

        # Each child fails in 1s (< 10s window), should exit after 3 failures
        time_values = []
        for i in range(4):
            time_values.append(float(i * 2))
            time_values.append(float(i * 2 + 1))
        time_iter = iter(time_values)

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 1
        assert counter[0] == 3


class TestValidateStandaloneAuth:
    """Direct unit tests for _validate_standalone_auth covering each branch."""

    def test_passphrase_and_unsafe_no_auth_raises(self):
        with pytest.raises(click.UsageError, match="mutually exclusive"):
            _validate_standalone_auth("secret", unsafe_no_auth=True, tls_insecure=False)

    def test_auto_generates_passphrase_when_none_provided(self, capsys):
        result = _validate_standalone_auth(None, unsafe_no_auth=False, tls_insecure=False)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Generated random passphrase" in capsys.readouterr().err

    def test_empty_string_treated_as_no_passphrase(self, capsys):
        result = _validate_standalone_auth("", unsafe_no_auth=False, tls_insecure=False)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Generated random passphrase" in capsys.readouterr().err

    def test_explicit_passphrase_returned_unchanged(self, capsys):
        result = _validate_standalone_auth("my-secret", unsafe_no_auth=False, tls_insecure=False)
        assert result == "my-secret"
        assert "Generated random passphrase" not in capsys.readouterr().err

    def test_unsafe_no_auth_returns_none(self):
        result = _validate_standalone_auth(None, unsafe_no_auth=True, tls_insecure=False)
        assert result is None

    def test_passphrase_with_tls_insecure_warns(self, capsys):
        _validate_standalone_auth("secret", unsafe_no_auth=False, tls_insecure=True)
        assert "authentication is active but TLS is disabled" in capsys.readouterr().err

    def test_unsafe_no_auth_with_tls_insecure_warns_unprotected(self, capsys):
        _validate_standalone_auth(None, unsafe_no_auth=True, tls_insecure=True)
        assert "completely unprotected" in capsys.readouterr().err

    def test_unsafe_no_auth_without_tls_insecure_warns_no_auth(self, capsys):
        _validate_standalone_auth(None, unsafe_no_auth=True, tls_insecure=False)
        captured = capsys.readouterr().err
        assert "running without authentication" in captured
        assert "completely unprotected" not in captured


class TestRunCommandAuthValidation:
    """Integration tests exercising auth validation through the Click CLI layer."""

    def _invoke(self, args, mock_serve=True):
        runner = click.testing.CliRunner()
        patches = []
        if mock_serve:
            patches.append(
                patch.object(run_mod, "_serve_with_exc_handling", return_value=0),
            )
            patches.append(
                patch(
                    "jumpstarter.config.exporter.ExporterConfigV1Alpha1.load_path",
                    return_value=MagicMock(),
                ),
            )

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = runner.invoke(run, args)
        return result

    def test_passphrase_and_unsafe_no_auth_mutually_exclusive(self):
        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--tls-grpc-listener", "1234",
            "--tls-grpc-insecure",
            "--passphrase", "secret",
            "--unsafe-no-auth",
        ])
        assert result.exit_code != 0
        assert "--passphrase and --unsafe-no-auth are mutually exclusive" in result.output

    def test_tls_grpc_listener_requires_tls_mode(self):
        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--tls-grpc-listener", "1234",
        ])
        assert result.exit_code != 0
        assert "--tls-grpc-listener requires either --tls-grpc-insecure" in result.output

    def test_auto_generated_passphrase_printed_to_stderr(self):
        runner = click.testing.CliRunner()
        mock_serve = MagicMock(return_value=0)
        with (
            patch.object(run_mod, "_serve_with_exc_handling", mock_serve),
            patch(
                "jumpstarter.config.exporter.ExporterConfigV1Alpha1.load_path",
                return_value=MagicMock(),
            ),
        ):
            result = runner.invoke(run, [
                "--exporter-config", "/dev/null",
                "--tls-grpc-listener", "1234",
                "--tls-grpc-insecure",
            ])
        assert result.exit_code == 0
        assert "Generated random passphrase" in result.stderr
        mock_serve.assert_called_once()
        forwarded_passphrase = mock_serve.call_args.kwargs["passphrase"]
        assert isinstance(forwarded_passphrase, str)
        assert len(forwarded_passphrase) > 0

    def test_passphrase_with_tls_insecure_warns_plaintext(self):
        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--tls-grpc-listener", "1234",
            "--tls-grpc-insecure",
            "--passphrase", "mysecret",
        ])
        assert result.exit_code == 0
        assert "authentication is active but TLS is disabled" in result.stderr

    def test_unsafe_no_auth_with_tls_insecure_warns_completely_unprotected(self):
        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--tls-grpc-listener", "1234",
            "--tls-grpc-insecure",
            "--unsafe-no-auth",
        ])
        assert result.exit_code == 0
        assert "completely unprotected" in result.stderr

    def test_unsafe_no_auth_without_tls_insecure_warns_no_auth(self, tmp_path):
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        cert_path.write_text("dummy cert")
        key_path.write_text("dummy key")

        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--tls-grpc-listener", "1234",
            "--tls-cert", str(cert_path),
            "--tls-key", str(key_path),
            "--unsafe-no-auth",
        ])
        assert result.exit_code == 0
        assert "running without authentication" in result.stderr
        assert "completely unprotected" not in result.stderr

    def test_unsafe_no_auth_without_listener_errors(self):
        result = self._invoke([
            "--exporter-config", "/dev/null",
            "--unsafe-no-auth",
        ])
        assert result.exit_code != 0
        assert "require --tls-grpc-listener" in result.output
