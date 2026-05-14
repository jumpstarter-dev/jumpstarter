"""Tests for the exporter run module, specifically rapid failure detection."""

import os
from unittest.mock import MagicMock, patch

import jumpstarter_cli.run as run_mod
from jumpstarter_cli.run import MAX_RAPID_FAILURES, RAPID_FAILURE_WINDOW


class TestRapidFailureDefaults:
    """Test that rapid failure configuration defaults are sensible."""

    def test_max_rapid_failures_default(self):
        assert MAX_RAPID_FAILURES == 5

    def test_rapid_failure_window_default(self):
        assert RAPID_FAILURE_WINDOW == 30


class TestRapidFailureEnvConfig:
    """Test that rapid failure thresholds can be configured via env vars."""

    def test_max_rapid_failures_from_env(self):
        with patch.dict(os.environ, {"JUMPSTARTER_MAX_RAPID_FAILURES": "10"}):
            import importlib

            importlib.reload(run_mod)
            try:
                assert run_mod.MAX_RAPID_FAILURES == 10
            finally:
                # Restore original values
                importlib.reload(run_mod)

    def test_rapid_failure_window_from_env(self):
        with patch.dict(os.environ, {"JUMPSTARTER_RAPID_FAILURE_WINDOW": "60"}):
            import importlib

            importlib.reload(run_mod)
            try:
                assert run_mod.RAPID_FAILURE_WINDOW == 60
            finally:
                # Restore original values
                importlib.reload(run_mod)


class TestServeWithExcHandlingRapidFailures:
    """Test rapid failure detection in _serve_with_exc_handling.

    These tests mock os.fork and _handle_parent/_handle_child to simulate
    the restart loop without actually forking processes.
    """

    def test_exits_after_max_rapid_failures(self):
        """When children fail rapidly MAX_RAPID_FAILURES times, the function should exit with code 1."""
        call_count = 0

        def mock_fork():
            nonlocal call_count
            call_count += 1
            return call_count  # Always return positive (parent path)

        def mock_handle_parent(pid):
            return None  # Simulate child exit code 0 -> restart

        # Simulate time.monotonic returning values that make each child
        # appear to fail within the rapid failure window.
        # Each fork call gets a start time, then _handle_parent returns
        # and time.monotonic is called again; the difference must be < RAPID_FAILURE_WINDOW.
        time_values = []
        for i in range(run_mod.MAX_RAPID_FAILURES + 1):
            time_values.append(float(i * 2))       # child_start_time
            time_values.append(float(i * 2 + 1))   # elapsed check (1s later, < 30s window)
        time_iter = iter(time_values)

        config = MagicMock()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 1
        assert call_count == run_mod.MAX_RAPID_FAILURES

    def test_resets_counter_after_long_running_child(self):
        """A child that runs longer than RAPID_FAILURE_WINDOW resets the rapid failure counter."""
        call_count = 0
        rapid_before_reset = run_mod.MAX_RAPID_FAILURES - 1

        def mock_fork():
            nonlocal call_count
            call_count += 1
            return call_count

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
        for i in range(run_mod.MAX_RAPID_FAILURES):
            time_values.append(float(base2 + i * 2))       # start
            time_values.append(float(base2 + i * 2 + 1))   # elapsed = 1s (rapid)

        time_iter = iter(time_values)
        config = MagicMock()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        # The function should exit due to rapid failures AFTER the reset
        # Total calls: (MAX-1) rapid + 1 long + MAX rapid
        assert exit_code == 1
        expected_calls = rapid_before_reset + 1 + run_mod.MAX_RAPID_FAILURES
        assert call_count == expected_calls

    def test_normal_exit_code_passed_through(self):
        """When _handle_parent returns a non-None exit code, it should be passed through."""

        def mock_fork():
            return 1

        def mock_handle_parent(pid):
            return 137  # Simulating killed by signal

        config = MagicMock()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", return_value=0.0),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 137

    def test_single_rapid_failure_does_not_exit(self):
        """A single rapid failure should not cause the main process to exit."""
        call_count = 0

        def mock_fork():
            nonlocal call_count
            call_count += 1
            return call_count

        def mock_handle_parent(pid):
            if call_count == 1:
                return None  # First child exits with 0 (rapid failure)
            return 0  # Second child exits normally with explicit code

        # First child: rapid failure (1s). Second child: normal exit code returned.
        time_values = [0.0, 1.0, 2.0, 3.0]
        time_iter = iter(time_values)
        config = MagicMock()

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch("time.monotonic", side_effect=lambda: next(time_iter)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        # Should exit with 0 from the second child, not 1 from rapid failure detection
        assert exit_code == 0
        assert call_count == 2
