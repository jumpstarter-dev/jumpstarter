"""Portable tests of the Windows supervisor's control and cleanup boundaries."""

import signal
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import jumpstarter_cli.run as run_mod


@pytest.fixture
def supervisor(monkeypatch):  # noqa: C901 - fake child/tree model the supervisor lifecycle
    state = SimpleNamespace(events=[], handlers={}, alive=False, joins=0, mode="normal")
    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    state.stop_signal = signal.SIGBREAK
    previous = {sig: object() for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK)}
    state.handlers.update(previous)
    state.previous = previous

    def set_handler(sig, handler):
        old = state.handlers[sig]
        state.handlers[sig] = handler
        return old

    monkeypatch.setattr(signal, "signal", set_handler)
    parent_pipe = MagicMock()
    child_pipe = MagicMock()
    parent_pipe.send.side_effect = lambda value: state.events.append(("send", value))
    parent_pipe.close.side_effect = lambda: state.events.append(("parent_pipe", "closed"))
    child_pipe.close.side_effect = lambda: state.events.append(("child_pipe", "closed"))

    class Child:
        pid = 123
        sentinel = 456
        exitcode = 0

        def start(self):
            state.events.append(("child", "started"))
            state.alive = True

        def is_alive(self):
            return state.alive

        def join(self, timeout):
            if not state.alive:
                return
            state.joins += 1
            if state.mode == "normal":
                state.alive = False
            elif state.joins == 1:
                state.handlers[state.stop_signal](state.stop_signal, None)
            elif state.mode == "graceful":
                state.alive = False

        def terminate(self):
            state.events.append(("child", "terminated"))
            state.alive = False

        def close(self):
            state.events.append(("child", "closed"))

    class Tree:
        def __init__(self, handle):
            assert handle == 456
            state.events.append(("tree", "assigned"))
            if state.mode == "assignment_failure":
                raise OSError("assignment rejected")

        def close(self):
            state.events.append(("tree", "closed"))
            state.alive = False

    context = MagicMock()
    context.Pipe.return_value = parent_pipe, child_pipe
    context.Process.return_value = Child()
    monkeypatch.setattr(run_mod.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setitem(sys.modules, "jumpstarter_core.process", SimpleNamespace(ChildProcessTree=Tree))
    state.config = MagicMock()
    state.config.model_dump_json.return_value = "{}"
    return state


def test_worker_cannot_start_before_tree_assignment(supervisor):
    assert run_mod._run_windows_child(supervisor.config) is None
    supervisor.config.model_dump_json.assert_called_once_with(by_alias=True)
    assert supervisor.events.index(("tree", "assigned")) < supervisor.events.index(("send", "start"))
    assert supervisor.events.index(("child_pipe", "closed")) < supervisor.events.index(("send", "start"))
    assert ("tree", "closed") in supervisor.events
    assert supervisor.handlers == supervisor.previous


@pytest.mark.parametrize("stop_name", ["SIGINT", "SIGTERM", "SIGBREAK"])
def test_graceful_stop_is_delivered_once_and_preserves_exit_status(supervisor, stop_name):
    supervisor.mode = "graceful"
    supervisor.stop_signal = getattr(signal, stop_name)
    assert run_mod._run_windows_child(supervisor.config) == 128 + supervisor.stop_signal
    assert supervisor.events.count(("send", supervisor.stop_signal)) == 1
    assert ("child", "terminated") not in supervisor.events
    assert supervisor.handlers == supervisor.previous


def test_deadline_closes_tree_and_restores_handlers(supervisor, monkeypatch):
    supervisor.mode = "hang"
    monkeypatch.setattr(run_mod, "_WINDOWS_STOP_TIMEOUT", 0)
    assert run_mod._run_windows_child(supervisor.config) == 128 + signal.SIGBREAK
    assert ("tree", "closed") in supervisor.events
    assert not supervisor.alive
    assert supervisor.handlers == supervisor.previous


def test_assignment_failure_never_releases_child(supervisor):
    supervisor.mode = "assignment_failure"
    with pytest.raises(OSError, match="assignment rejected"):
        run_mod._run_windows_child(supervisor.config)
    assert ("send", "start") not in supervisor.events
    assert ("child", "terminated") in supervisor.events
    assert not supervisor.alive
    assert supervisor.handlers == supervisor.previous


def test_spawned_child_restores_logging_before_serving(monkeypatch):
    import jumpstarter.logging

    monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(signal, "signal", MagicMock())
    setup = MagicMock()
    monkeypatch.setattr(jumpstarter.logging, "setup_logging", setup)
    serve = MagicMock(side_effect=SystemExit(37))
    monkeypatch.setattr(run_mod, "_handle_child", serve)
    control = MagicMock()
    control.recv.return_value = "start"
    config = ('{"apiVersion":"jumpstarter.dev/v1alpha1","kind":"ExporterConfig",'
              '"metadata":{"name":"test","namespace":"default"},"export":{},'
              '"exitOnLeaseEnd":true,"failureDetection":{"maxRapidFailures":3,"rapidFailureWindow":7}}')
    with pytest.raises(SystemExit) as result:
        run_mod._windows_child(config, (), control, ("json", 10))
    assert result.value.code == 37
    setup.assert_called_once_with(component="exporter", log_format="json", level=10)
    assert serve.call_args.kwargs == {"control": control}
    spawned_config = serve.call_args.args[0]
    assert spawned_config.exit_on_lease_end is True
    assert spawned_config.failure_detection.max_rapid_failures == 3
    assert spawned_config.failure_detection.rapid_failure_window == 7
    control.close.assert_called_once()
