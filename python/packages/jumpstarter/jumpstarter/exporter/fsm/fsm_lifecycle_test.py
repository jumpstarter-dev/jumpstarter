"""Exercise the production lifecycle with real session sockets and controlled I/O."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
import structlog.contextvars
from anyio import Event, create_task_group, fail_after, sleep_forever
from anyio.lowlevel import checkpoint
from google.protobuf import empty_pb2
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc

from jumpstarter.common import ExporterStatus
from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.driver import Driver, export
from jumpstarter.exporter.exporter import Exporter
from jumpstarter.exporter.fsm import AwaitingLeaseRelease, BeforeLeaseHook, ClosingLease, LeaseReady, ShutdownMode
from jumpstarter.exporter.fsm.lease_context import LeaseContext
from jumpstarter.exporter.hooks import HookExecutionError, HookExecutor
from jumpstarter.fsm import ExitFSM

pytestmark = pytest.mark.anyio


class PingDriver(Driver):
    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"

    @export
    def ping(self) -> str:
        return "pong"


@dataclass(kw_only=True)
class LocalExporter(Exporter):
    trace: list[tuple[str, str]] = field(default_factory=list)
    leases: list[LeaseContext] = field(default_factory=list)
    changed: Event = field(default_factory=Event)
    closing: Event = field(default_factory=Event)
    closed: Event = field(default_factory=Event)
    close_gate: Event | None = None
    controller: MagicMock = field(default_factory=lambda: MagicMock(ReleaseLease=AsyncMock()))
    _last_status_lease: str = ""

    @asynccontextmanager
    async def session(self):
        yield  # Registration RPCs have their own tests; lease sessions below are real.

    @asynccontextmanager
    async def _controller_stub(self):
        yield self.controller

    async def _send_report_status_rpc(self, request) -> bool:
        return True

    async def report_status(
        self, status: ExporterStatus, message: str = "", *, lease: LeaseContext | None = None
    ) -> None:
        await super().report_status(status, message, lease=lease)
        self._last_status_lease = lease.lease_name if lease else ""
        self.trace.append((status.name, self._last_status_lease))
        changed, self.changed = self.changed, Event()
        changed.set()

    async def wait_status(self, status: ExporterStatus, lease_name: str | None = None) -> None:
        while True:
            changed = self.changed
            if self._exporter_status == status and (lease_name is None or self._last_status_lease == lease_name):
                return
            await changed.wait()

    @asynccontextmanager
    async def session_for_lease(self):
        state = self.fsm.runner.current_state if self.fsm.runner else None
        lease = getattr(state, "admitting", None)
        assert isinstance(lease, LeaseContext)
        self.trace.append(("open", lease.lease_name))
        try:
            async with super().session_for_lease() as session:
                self.leases.append(lease)
                try:
                    yield session
                finally:
                    self.closing.set()
                    if self.close_gate is not None:
                        with fail_after(3, shield=True):
                            await self.close_gate.wait()
        finally:
            self.trace.append(("close", lease.lease_name))
            self.closed.set()

    async def _retry_stream(self, stream_name, stream_factory, send_tx, backoff=0.5, outage_budget=None):
        try:
            await sleep_forever()
        finally:
            self.trace.append(("stream closed", stream_name))


def make_exporter(**kwargs) -> LocalExporter:
    return LocalExporter(
        channel_factory=AsyncMock(), device_factory=PingDriver, labels={}, exporter_name="test-exporter", **kwargs
    )


def hooks() -> HookExecutor:
    hook = HookInstanceConfigV1Alpha1(script="echo hook", timeout=3)
    return HookExecutor(config=HookConfigV1Alpha1(before_lease=hook, after_lease=hook))


async def hook_driver_call(lease: LeaseContext) -> None:
    assert lease.session is not None
    assert Path(lease.socket_path).exists() and Path(lease.hook_socket_path).exists()
    async with grpc.aio.insecure_channel(f"unix://{lease.hook_socket_path}") as channel:
        stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        await stub.DriverCall(
            jumpstarter_pb2.DriverCallRequest(uuid=str(lease.session.root_device.uuid), method="ping"), timeout=2
        )


async def test_end_session_keeps_reservation_socket_and_final_status(monkeypatch):
    executor = hooks()
    after_calls: list[str] = []

    async def before(lease):
        await hook_driver_call(lease)

    async def after(lease):
        after_calls.append(lease.lease_name)
        await hook_driver_call(lease)

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)
    exporter = make_exporter(hook_executor=executor)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            await exporter.fsm.inputs.send(
                jumpstarter_pb2.StatusResponse(leased=True, lease_name="A", client_name="client")
            )
            await exporter.wait_status(ExporterStatus.LEASE_READY)
            lease = exporter.leases[0]
            async with grpc.aio.insecure_channel(f"unix://{lease.socket_path}") as channel:
                stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
                assert (await stub.EndSession(jumpstarter_pb2.EndSessionRequest(), timeout=2)).success
                await exporter.wait_status(ExporterStatus.AVAILABLE)
                assert not exporter.closed.is_set()
                assert exporter.fsm.runner is not None
                assert isinstance(exporter.fsm.runner.current_state, AwaitingLeaseRelease)
                assert exporter.fsm.runner.current_state.lease is lease
                status = await stub.GetStatus(empty_pb2.Empty(), timeout=2)
                assert status.status == ExporterStatus.AVAILABLE.to_proto()
                await stub.EndSession(jumpstarter_pb2.EndSessionRequest(), timeout=2)
                exporter.stop(wait_for_lease_exit=True)
                await checkpoint()
                assert not exporter.closed.is_set()
                exporter.fsm.inputs.publish_lease_assignment(None)
    assert after_calls == ["A"]
    exporter.controller.ReleaseLease.assert_not_awaited()
    assert exporter.closed.is_set() and not Path(lease.socket_path).exists()
    assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ExitFSM)
    assert ("stream closed", "Status") in exporter.trace and ("stream closed", "Listen") in exporter.trace


async def test_drain_alone_does_not_finish_active_lease():
    exporter = make_exporter()
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await exporter.wait_status(ExporterStatus.LEASE_READY)
            lease = exporter.leases[0]
            exporter.stop(wait_for_lease_exit=True)
            exporter.fsm.inputs.publish_lease_assignment("A", "reevaluated")
            while lease.client_name != "reevaluated":
                await checkpoint()
            assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, LeaseReady)
            assert not exporter.closed.is_set() and not lease.end_session_requested
            exporter.fsm.inputs.publish_lease_assignment("B")
    assert [lease.lease_name for lease in exporter.leases] == ["A"]
    assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ExitFSM)


@pytest.mark.parametrize("replacement", ["A", "C"])
async def test_reassignment_waits_for_hooks_and_full_teardown(monkeypatch, replacement):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor, close_gate=Event())
    entered, release = Event(), Event()
    contexts = []

    async def before(lease):
        exporter.trace.append(("before", lease.lease_name))
        contexts.append(structlog.contextvars.get_contextvars())
        await hook_driver_call(lease)
        if len(exporter.leases) == 1:
            entered.set()
            with fail_after(3):
                await release.wait()
        exporter.trace.append(("before done", lease.lease_name))

    async def after(lease):
        await hook_driver_call(lease)
        exporter.trace.append(("after", lease.lease_name))

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)
    with fail_after(8):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A", "client", {"build_id": "42"})
            await entered.wait()
            old = exporter.leases[0]
            exporter.fsm.inputs.publish_lease_assignment(None if replacement == "A" else "B")
            exporter.fsm.inputs.publish_lease_assignment(replacement, "new-client")
            await checkpoint()
            assert ("after", "A") not in exporter.trace
            release.set()
            await exporter.closing.wait()
            assert len(exporter.leases) == 1 and not exporter.closed.is_set()
            assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ClosingLease)
            assert exporter.close_gate is not None
            exporter.close_gate.set()
            await exporter.wait_status(ExporterStatus.LEASE_READY, replacement)
            new = exporter.leases[1]
            old.request_end_session()
            await checkpoint()
            assert not new.end_session_requested
            exporter.stop()
    assert contexts[0]["build_id"] == "42" and "build_id" not in contexts[1]
    assert [lease.lease_name for lease in exporter.leases] == ["A", replacement]
    trace = exporter.trace
    assert trace.index(("before done", "A")) < trace.index(("after", "A")) < trace.index(("close", "A"))
    assert [event for event in trace if event[0] in ("open", "close")] == [
        ("open", "A"),
        ("close", "A"),
        ("open", replacement),
        ("close", replacement),
    ]


@pytest.mark.parametrize("phase", ["before_lease", "after_lease"])
@pytest.mark.parametrize("policy", ["warn", "endLease", "exit", "unexpected"])
async def test_hook_failure_policy_and_cleanup_eligibility(monkeypatch, phase, policy):  # noqa: C901
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    calls: list[str] = []

    async def execute(config, lease, source):
        called = "before_lease" if source.name == "BEFORE_LEASE_HOOK" else "after_lease"
        calls.append(called)
        await hook_driver_call(lease)
        if called != phase:
            return None
        if policy == "warn":
            return "warning"
        if policy == "unexpected":
            raise RuntimeError("unexpected failure")
        raise HookExecutionError("hook failed", policy, phase)

    monkeypatch.setattr(executor, "_execute_hook", execute)
    with fail_after(8):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")  # Cleanup eligibility is not an assigned client name.
            if phase == "after_lease" or policy == "warn":
                await exporter.wait_status(ExporterStatus.LEASE_READY)
                if phase == "before_lease" and policy == "warn":
                    session = exporter.leases[0].session
                    assert session is not None and session._status_message.startswith("[HOOK_WARNING]")
                exporter.leases[0].request_end_session()
            if policy == "warn":
                await exporter.wait_status(ExporterStatus.AVAILABLE)
                if phase == "after_lease":
                    session = exporter.leases[0].session
                    assert session is not None and session._status_message.startswith("[HOOK_WARNING]")
            else:
                failed = (
                    ExporterStatus.BEFORE_LEASE_HOOK_FAILED
                    if phase == "before_lease"
                    else ExporterStatus.AFTER_LEASE_HOOK_FAILED
                )
                await exporter.wait_status(failed)
                assert not exporter.closed.is_set()
                if policy == "endLease":
                    await exporter.closed.wait()
                    exporter.fsm.inputs.publish_lease_assignment("A", "duplicate", {"job": "changed"})
                elif policy == "exit":
                    assert exporter.exit_code == 1
                    shutdown = exporter.fsm.inputs.snapshot_and_watch()[0].shutdown
                    assert shutdown is not None and shutdown.mode == ShutdownMode.DRAIN
                else:
                    exporter.leases[0].request_end_session()
                    if phase == "before_lease":
                        await exporter.wait_status(ExporterStatus.AVAILABLE)
            exporter.stop()
    assert calls == (
        ["before_lease"]
        if phase == "before_lease" and policy in ("endLease", "exit")
        else ["before_lease", "after_lease"]
    )
    assert exporter.controller.ReleaseLease.await_count == (1 if policy == "endLease" else 0)
    assert len(exporter.leases) == 1


@pytest.mark.parametrize("failure", ["auth", "invalid", "outage"])
async def test_terminal_stream_failure_defers_shutdown_and_overrides_hook_exit(monkeypatch, failure):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    entered, finish_hook, fail_stream, failed = Event(), Event(), Event(), Event()
    original_retry = Exporter._retry_stream.__get__(exporter, Exporter)

    async def before(lease):
        entered.set()
        with fail_after(3):
            await finish_hook.wait()

    async def after(lease):
        exporter.trace.append(("after", lease.lease_name))

    async def stream(controller):
        await fail_stream.wait()
        if failure == "outage":
            return
        code = grpc.StatusCode.UNAUTHENTICATED if failure == "auth" else grpc.StatusCode.INVALID_ARGUMENT
        raise grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), "terminal failure")
        yield

    async def retry(name, factory, sender, backoff=0.5, outage_budget=None):
        if name == "Listen":
            await sleep_forever()
        try:
            await original_retry(name, stream, sender, outage_budget=0 if failure == "outage" else None)
        finally:
            failed.set()

    monkeypatch.setattr(exporter, "_retry_stream", retry)
    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await entered.wait()
            exporter.stop(wait_for_lease_exit=True, exit_code=1, should_unregister=True)
            fail_stream.set()
            await failed.wait()
            assert not exporter.closed.is_set()
            finish_hook.set()
    assert exporter.exit_code == (1 if failure == "invalid" else 75)
    assert ("after", "A") in exporter.trace and exporter.closed.is_set()
    shutdown = exporter.fsm.inputs.snapshot_and_watch()[0].shutdown
    assert shutdown is not None and shutdown.should_unregister is False
    exporter.controller.ReleaseLease.assert_not_awaited()


async def test_exit_on_lease_end_stops_sidecar_after_teardown(monkeypatch):
    exporter = make_exporter(exit_on_lease_end=True, close_gate=Event())
    sidecar = MagicMock(side_effect=lambda: exporter.trace.append(("sidecar", "")))
    monkeypatch.setattr("jumpstarter.exporter.exporter.shutdown_runtime_sidecar", sidecar)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            await exporter.wait_status(ExporterStatus.AVAILABLE)
            assert exporter.fsm.inputs.snapshot_and_watch()[0].shutdown is None
            exporter.fsm.inputs.publish_lease_assignment("A")
            await exporter.wait_status(ExporterStatus.LEASE_READY)
            exporter.fsm.inputs.publish_lease_assignment("B")
            await exporter.closing.wait()
            sidecar.assert_not_called()
            assert exporter.close_gate is not None
            exporter.close_gate.set()
    sidecar.assert_called_once()
    assert exporter.trace.index(("close", "A")) < exporter.trace.index(("sidecar", ""))
    assert len(exporter.leases) == 1


@pytest.mark.parametrize("failure", ["setup", "close", "background"])
async def test_failure_unwinds_resources_and_never_admits_replacement(monkeypatch, failure):  # noqa: C901
    closed: list[str] = []
    fail_stream = Event()

    class FailingDriver(PingDriver):
        def reset(self):
            if failure == "setup":
                raise RuntimeError("setup failed")

        def close(self):
            closed.append("driver")
            if failure == "close":
                raise RuntimeError("close failed")

    exporter = make_exporter()
    exporter.device_factory = FailingDriver

    async def retry(name, factory, sender, backoff=0.5, outage_budget=None):
        if name == "Status":
            await fail_stream.wait()
            raise RuntimeError("background failed")
        await sleep_forever()

    if failure == "background":
        monkeypatch.setattr(exporter, "_retry_stream", retry)

    async def serve() -> None:
        with pytest.raises(ExceptionGroup):
            await exporter.serve()

    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            if failure != "setup":
                await exporter.wait_status(ExporterStatus.LEASE_READY)
                if failure == "background":
                    fail_stream.set()
                else:
                    exporter.fsm.inputs.publish_lease_assignment("B")
    assert closed == ["driver"]
    assert len(exporter.leases) <= 1
    assert exporter.fsm.runner is not None and not isinstance(exporter.fsm.runner.current_state, ExitFSM)
    assert exporter._background_done.is_set()
    if exporter.leases:
        assert not Path(exporter.leases[0].socket_path).exists()


async def test_standalone_keeps_its_hook_and_shutdown_path(monkeypatch):
    executor = hooks()
    calls = []

    async def execute(config, lease, source):
        calls.append(source.name)
        await hook_driver_call(lease)

    monkeypatch.setattr(executor, "_execute_hook", execute)
    exporter = make_exporter(hook_executor=executor)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve_standalone_tcp, "127.0.0.1", 0)
            await exporter.wait_status(ExporterStatus.LEASE_READY)
            exporter.stop()
    assert calls == ["BEFORE_LEASE_HOOK", "AFTER_LEASE_HOOK"]
    assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ExitFSM)


@pytest.mark.parametrize("finish", ["end_session", "stop"])
async def test_standalone_defers_requests_and_runs_cleanup_once(monkeypatch, finish):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    entered, release = Event(), Event()
    calls = []

    async def before(lease):
        calls.append("before")
        entered.set()
        with fail_after(3):
            await release.wait()
        calls.append("before done")

    async def after(lease):
        calls.append("after")
        await hook_driver_call(lease)

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve_standalone_tcp, "127.0.0.1", 0)
            await entered.wait()
            assert exporter.fsm.runner is not None
            state = exporter.fsm.runner.current_state
            assert isinstance(state, BeforeLeaseHook)
            lease = state.lease
            assert isinstance(lease, LeaseContext)
            if finish == "end_session":
                lease.request_end_session()
                lease.request_end_session()
            else:
                exporter.stop(wait_for_lease_exit=True)
            await checkpoint()
            assert calls == ["before"]
            release.set()
            if finish == "end_session":
                await exporter.wait_status(ExporterStatus.AVAILABLE)
                assert Path(lease.socket_path).exists()
                assert exporter.fsm.runner is not None
                assert isinstance(exporter.fsm.runner.current_state, AwaitingLeaseRelease)
                exporter.stop()
    assert calls == ["before", "before done", "after"]
    assert lease is not None and not Path(lease.socket_path).exists()
    assert isinstance(exporter.channel_factory, AsyncMock)
    exporter.channel_factory.assert_not_awaited()


@pytest.mark.parametrize("policy", ["endLease", "exit"])
async def test_standalone_before_failure_skips_after_hook(monkeypatch, policy):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    after = AsyncMock()

    async def before(lease):
        raise HookExecutionError("failed", policy, "before_lease")

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve_standalone_tcp, "127.0.0.1", 0)
            if policy == "endLease":
                await exporter.wait_status(ExporterStatus.BEFORE_LEASE_HOOK_FAILED)
                exporter.stop()
    after.assert_not_awaited()
    assert isinstance(exporter.channel_factory, AsyncMock)
    exporter.channel_factory.assert_not_awaited()
    assert exporter.exit_code == (1 if policy == "exit" else None)
    assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ExitFSM)


async def test_release_during_setup_skips_hooks_and_listen(monkeypatch):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    prepared, finish_setup = Event(), Event()
    session_for_lease = exporter.session_for_lease
    before, after = AsyncMock(), AsyncMock()
    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", after)

    @asynccontextmanager
    async def prepare():
        async with session_for_lease() as session:
            prepared.set()
            await finish_setup.wait()
            yield session

    monkeypatch.setattr(exporter, "session_for_lease", prepare)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await prepared.wait()
            exporter.fsm.inputs.publish_lease_assignment(None)
            exporter.stop(wait_for_lease_exit=True)
            finish_setup.set()
    before.assert_not_awaited()
    after.assert_not_awaited()
    assert ("stream closed", "Listen") not in exporter.trace
    assert exporter.closed.is_set()


async def test_connections_finish_before_session_teardown(monkeypatch):
    exporter = make_exporter()
    connected, finished = Event(), Event()

    async def retry(name, factory, sender, backoff=0.5, outage_budget=None):
        if name == "Listen":
            await sender.send(jumpstarter_pb2.ListenResponse(router_endpoint="router", router_token="token"))
        await sleep_forever()

    async def connection(path, endpoint, token, tls, grpc_options):
        assert (endpoint, token) == ("router", "token")
        connected.set()
        try:
            await sleep_forever()
        finally:
            assert Path(path).exists() and not exporter.closed.is_set()
            with fail_after(1, shield=True):
                await checkpoint()
                finished.set()

    monkeypatch.setattr(exporter, "_retry_stream", retry)
    monkeypatch.setattr(exporter, "_handle_client_conn", connection)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await connected.wait()
            exporter.stop()
    assert finished.is_set() and exporter.closed.is_set()


@pytest.mark.parametrize("failed", [False, True])
async def test_remote_connections_wait_for_published_before_hook_outcome(monkeypatch, failed):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    entered, finish_hook, dialed, connected = Event(), Event(), Event(), Event()

    async def before(lease):
        await hook_driver_call(lease)
        entered.set()
        with fail_after(3):
            await finish_hook.wait()
        if failed:
            raise HookExecutionError("failed", "exit", "before_lease")

    async def retry(name, factory, sender, backoff=0.5, outage_budget=None):
        if name == "Listen":
            await sender.send(jumpstarter_pb2.ListenResponse(router_endpoint="router", router_token="token"))
            dialed.set()
        await sleep_forever()

    async def connection(*args):
        expected = ExporterStatus.BEFORE_LEASE_HOOK_FAILED if failed else ExporterStatus.LEASE_READY
        assert exporter._exporter_status == expected
        connected.set()
        await sleep_forever()

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr(executor, "execute_after_lease_hook", AsyncMock(return_value=None))
    monkeypatch.setattr(exporter, "_retry_stream", retry)
    monkeypatch.setattr(exporter, "_handle_client_conn", connection)
    with fail_after(5):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await entered.wait()
            await dialed.wait()
            assert not connected.is_set()
            finish_hook.set()
            await connected.wait()
            exporter.stop()
    assert exporter.closed.is_set()


@pytest.mark.parametrize("change", ["same_name", "replacement", "stop_now"])
async def test_release_retries_check_assignment_generation_and_shutdown(monkeypatch, change):
    exporter = make_exporter()
    lease = LeaseContext("A")
    exporter.fsm.inputs.publish_lease_assignment("A")
    assignment = exporter.fsm.inputs.snapshot_and_watch()[0].lease_assignment
    assert assignment is not None
    error = grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, grpc.aio.Metadata(), grpc.aio.Metadata(), "retry")
    exporter.controller.ReleaseLease.side_effect = error

    async def backoff(delay):
        if change == "stop_now":
            exporter.stop()
        else:
            exporter.fsm.inputs.publish_lease_assignment(None)
            exporter.fsm.inputs.publish_lease_assignment("A" if change == "same_name" else "B")

    monkeypatch.setattr("anyio.sleep", backoff)
    with fail_after(2):
        await exporter._request_lease_release(lease, assignment.key)
    exporter.controller.ReleaseLease.assert_awaited_once()
    assert exporter.controller.ReleaseLease.await_args.args[0].name == "A"


async def test_stop_now_is_bounded_while_release_rpc_is_stalled(monkeypatch):
    executor = hooks()
    exporter = make_exporter(hook_executor=executor)
    releasing = Event()

    async def before(lease):
        raise HookExecutionError("failed", "endLease", "before_lease")

    async def release(request, **kwargs):
        releasing.set()
        await sleep_forever()

    async def visibility_delay(delay):
        await checkpoint()

    monkeypatch.setattr(executor, "execute_before_lease_hook", before)
    monkeypatch.setattr("jumpstarter.exporter.fsm.states.sleep", visibility_delay)
    monkeypatch.setattr("jumpstarter.exporter.exporter._LEASE_RELEASE_TIMEOUT", 0.05)
    exporter.controller.ReleaseLease.side_effect = release
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            exporter.fsm.inputs.publish_lease_assignment("A")
            await releasing.wait()
            exporter.stop()
    assert exporter.closed.is_set()
    assert exporter.fsm.runner is not None and isinstance(exporter.fsm.runner.current_state, ExitFSM)
