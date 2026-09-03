"""Tests for the exporter run module, specifically rapid failure detection."""

import multiprocessing
import os
import signal
import threading
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import coverage
import pytest
from click.testing import CliRunner

import jumpstarter_cli.run as run_mod


def _make_config(max_rapid_failures=5, rapid_failure_window=60, exit_on_lease_end=False):
    """Create a mock config with failure_detection settings."""
    config = MagicMock()
    config.failure_detection.max_rapid_failures = max_rapid_failures
    config.failure_detection.rapid_failure_window = rapid_failure_window
    config.exit_on_lease_end = exit_on_lease_end
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
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: next(time_iter))),
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
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: next(time_iter))),
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
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: 0.0)),
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
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: next(time_iter))),
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
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: next(time_iter))),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 1
        assert counter[0] == 3


class TestExitOnLeaseEnd:
    """Test exit_on_lease_end prevents parent from restarting child."""

    def test_no_restart_when_exit_on_lease_end(self):
        """Parent returns 0 immediately when child exits 0 with exit_on_lease_end."""
        config = _make_config(exit_on_lease_end=True)

        def mock_fork():
            return 1

        def mock_handle_parent(pid):
            return None  # Child exited 0

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: 0.0)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 0

    def test_nonzero_exit_still_passes_through(self):
        """Non-zero child exit code passes through even with exit_on_lease_end."""
        config = _make_config(exit_on_lease_end=True)

        def mock_fork():
            return 1

        def mock_handle_parent(pid):
            return 137  # Killed by signal

        with (
            patch.object(run_mod, "_handle_parent", side_effect=mock_handle_parent),
            patch("os.fork", side_effect=mock_fork),
            patch.object(run_mod, "time", types.SimpleNamespace(monotonic=lambda: 0.0)),
        ):
            exit_code = run_mod._serve_with_exc_handling(config)

        assert exit_code == 137


@pytest.mark.parametrize("supervisor_code", [0, 1, 75])
def test_run_uses_supervisor_exit_code(monkeypatch, tmp_path, supervisor_code):
    monkeypatch.setattr(run_mod, "_serve_with_exc_handling", lambda *args: supervisor_code)
    config = tmp_path / "exporter.yaml"
    config.write_text(
        "apiVersion: jumpstarter.dev/v1alpha1\nkind: ExporterConfig\n"
        "metadata:\n  namespace: default\n  name: test\n"
    )

    result = CliRunner().invoke(run_mod.run, ["--exporter-config", str(config)])
    assert result.exit_code == supervisor_code, result.output


class _RestartExporter:
    counter_path: Path
    seen_path: Path
    fail_serve = False

    def __init__(self, **kwargs):
        self.token = kwargs["token"]
        self.on_token_rotated = kwargs["on_token_rotated"]
        self.exit_code = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    def stop(self, **kwargs):
        pass

    async def serve(self):
        first_run = not self.counter_path.exists()
        self.counter_path.touch()
        if first_run:
            await self.on_token_rotated("rotated-token")
            if self.fail_serve:
                raise RuntimeError("restart after serving failed")
        else:
            self.seen_path.write_text(self.token)
            self.exit_code = 3


@contextmanager
def _restore_signal_handlers():
    saved = {sig: signal.getsignal(sig) for sig in
             (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGCHLD)}
    try:
        yield
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize("storage", ["writable", "read-only", "memory-only"])
@pytest.mark.parametrize("fail_serve", [False, True])
def test_rotated_token_survives_child_restart(tmp_path, monkeypatch, storage, fail_serve):
    import jumpstarter.exporter as exporter_pkg
    from jumpstarter.config.common import ObjectMeta
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1
    from jumpstarter.config.tls import TLSConfigV1Alpha1

    config_file = tmp_path / "exporter.yaml"
    cfg = ExporterConfigV1Alpha1(
        metadata=ObjectMeta(name="probe", namespace="default"),
        endpoint="localhost:1",
        token="startup-token",
        tls=TLSConfigV1Alpha1(insecure=True),
    )
    ExporterConfigV1Alpha1.save(cfg, path=str(config_file))
    config = ExporterConfigV1Alpha1.load_path(config_file)
    if storage == "memory-only":
        config.path = None
    elif storage == "read-only":
        def deny_save(*args):
            raise PermissionError("read-only config")
        monkeypatch.setattr(ExporterConfigV1Alpha1, "_save_rotated_token", deny_save)

    _RestartExporter.counter_path = tmp_path / "runs"
    _RestartExporter.seen_path = tmp_path / "seen"
    _RestartExporter.fail_serve = fail_serve
    monkeypatch.setattr(exporter_pkg, "Exporter", _RestartExporter)
    monkeypatch.setattr(run_mod, "start_metrics_server", lambda _addr: ("probe", None))
    handle_child = run_mod._handle_child

    def exit_child(*args, **kwargs):
        exit_code = 1
        try:
            # The inherited Python 3.14 tracer cannot safely trace except* after fork.
            # Keep collecting in the parent while exercising the real child path.
            if (collector := coverage.Coverage.current()) is not None:
                collector.stop()
            handle_child(*args, **kwargs)
        except SystemExit as exc:
            exit_code = (exc.code or 0) if isinstance(exc.code, (int, type(None))) else 1
        finally:
            # Unexpected exceptions must never unwind into the parent's pytest run.
            os._exit(exit_code)

    monkeypatch.setattr(run_mod, "_handle_child", exit_child)

    with _restore_signal_handlers():
        exit_code = run_mod._serve_with_exc_handling(config)

    assert exit_code == 3
    assert config.token == "rotated-token"
    assert (tmp_path / "seen").read_text() == "rotated-token"
    expected = "rotated-token" if storage == "writable" else "startup-token"
    assert ExporterConfigV1Alpha1.load_path(config_file).token == expected


def test_drains_pending_token_after_child_exit(monkeypatch):
    config = types.SimpleNamespace(token="startup-token")
    reader, writer = multiprocessing.Pipe(duplex=False)
    result = {}

    def child_exit(_pid):
        writer.send("last-rotated-token")
        return 75

    monkeypatch.setattr(run_mod, "_handle_parent", child_exit)
    # The writer stays open, as if a grandchild inherited it, so only the stop signal ends the receiver.
    supervisor = threading.Thread(
        target=lambda: result.update(code=run_mod._handle_parent_with_token_updates(123, config, reader)),
        daemon=True,
    )
    supervisor.start()
    supervisor.join(5)
    writer.close()

    assert not supervisor.is_alive()
    assert result["code"] == 75
    assert config.token == "last-rotated-token"
