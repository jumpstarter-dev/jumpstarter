import base64
import errno
import json
import logging
import stat
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import grpc
import pytest
import yaml
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.exporter import Exporter
from jumpstarter.exporter.metrics_stream import MetricsStreamClient
from jumpstarter.exporter.telemetry import TelemetryLogHandler


def _make_jwt(payload: dict) -> str:
    header = {"alg": "ES256", "typ": "JWT"}
    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    claims = {"iss": "https://controller.example.com", "sub": "exporter:default:test-exporter:uid", **payload}
    p_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{h_b64}.{p_b64}.dummy_signature"


class FakeRpcError(grpc.aio.AioRpcError):
    def __init__(self, code, details="error"):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


@pytest.mark.anyio
async def test_rotation_updates_token_callback_and_telemetry():
    mock_ctrl = AsyncMock()
    mock_ctrl.GetServiceEndpoints.return_value = jumpstarter_pb2.GetServiceEndpointsResponse(
        telemetry_endpoints=[jumpstarter_pb2.TelemetryEndpoint(endpoint="telemetry:443")]
    )
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    telemetry = MagicMock()
    telemetry.PushLogs = AsyncMock()
    metrics_streams = []

    def metrics_stream(**kwargs):
        metrics_streams.append(kwargs)
        raise RuntimeError("stream refused")

    telemetry.MetricsStream = metrics_stream

    async def telemetry_tokens(handler: TelemetryLogHandler, stream: MetricsStreamClient):
        handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None))
        await handler._flush()
        attempts = len(metrics_streams)
        async with anyio.create_task_group() as tg:
            tg.start_soon(stream.run)
            with anyio.fail_after(1):
                while len(metrics_streams) == attempts:
                    await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
        return telemetry.PushLogs.call_args.kwargs["metadata"], metrics_streams[-1]["metadata"]

    on_rotated = AsyncMock()
    exporter = _exporter_with_token("old-token", on_token_rotated=on_rotated, tls=TLSConfigV1Alpha1(insecure=True))
    with (
        patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl),
        patch("jumpstarter.exporter.exporter.grpc.aio.insecure_channel"),
        patch("jumpstarter.exporter.exporter.telemetry_pb2_grpc.TelemetryServiceStub", return_value=telemetry),
    ):
        await exporter._setup_telemetry()
        handler, stream = exporter._telemetry_handler, exporter._metrics_stream
        assert handler is not None and stream is not None
        try:
            old = [("authorization", "Bearer old-token")]
            assert await telemetry_tokens(handler, stream) == (old, old)
            token = await exporter.rotate_token()
            new = [("authorization", "Bearer rotated-token-value")]
            assert await telemetry_tokens(handler, stream) == (new, new)
        finally:
            logging.getLogger().removeHandler(handler)

    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    on_rotated.assert_awaited_once_with("rotated-token-value")


@pytest.mark.anyio
async def test_exporter_rotate_token_sync_callback():
    mock_ctrl = AsyncMock()
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    recorded_tokens = []

    def sync_on_rotated(token: str) -> None:
        recorded_tokens.append(token)

    exporter = _exporter_with_token("old-token", on_token_rotated=sync_on_rotated)

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        token = await exporter.rotate_token()

    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    assert recorded_tokens == ["rotated-token-value"]
    mock_ctrl.RotateToken.assert_awaited_once()


@pytest.mark.anyio
async def test_exporter_rotate_token_callback_failure():
    mock_ctrl = AsyncMock()
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    on_rotated = AsyncMock(side_effect=RuntimeError("disk full or save failed"))

    exporter = _exporter_with_token("old-token", on_token_rotated=on_rotated)

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        token = await exporter.rotate_token()

    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    on_rotated.assert_awaited_once_with("rotated-token-value")
    mock_ctrl.RotateToken.assert_awaited_once()


@pytest.mark.anyio
async def test_exporter_token_refresh_loop_near_expiry():
    now = time.time()
    token = _make_jwt({"iat": now - 100, "exp": now + 1})

    mock_ctrl = AsyncMock()
    next_token = _make_jwt({"iat": now, "exp": now + 100000})
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token=next_token)

    exporter = _exporter_with_token(token)

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        with anyio.move_on_after(0.5) as cancel_scope:
            await exporter._token_refresh_loop()

    assert cancel_scope.cancel_called
    assert exporter.token == next_token
    mock_ctrl.RotateToken.assert_awaited_once()


class StopRefreshLoop(Exception):
    pass


def _exporter_with_token(token: str, **kwargs) -> Exporter:
    channel = MagicMock(spec=grpc.aio.Channel)
    channel.close = AsyncMock()
    return Exporter(
        exporter_name="test-exporter",
        token=token,
        channel_factory=AsyncMock(return_value=channel),
        device_factory=MagicMock(),
        **kwargs,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("error,retry_delay", [
    pytest.param(FakeRpcError(grpc.StatusCode.UNIMPLEMENTED), 0.5, id="controller-upgrade"),
    pytest.param(FakeRpcError(grpc.StatusCode.PERMISSION_DENIED), 0.5, id="denied"),
    pytest.param(FakeRpcError(grpc.StatusCode.UNAVAILABLE), 1.0, id="unavailable"),
    pytest.param(FakeRpcError(grpc.StatusCode.ABORTED), 1.0, id="concurrent-rotation"),
    pytest.param(RuntimeError("unexpected"), 1.0, id="unexpected"),
])
async def test_refresh_retries_after_rotation_failure(error, retry_delay):
    now = time.time()
    exporter = _exporter_with_token(_make_jwt({"iat": now - 100, "exp": now + 1}))
    next_token = _make_jwt({"iat": now, "exp": now + 100000})
    attempts = 0

    async def rotate():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error
        exporter.token = next_token
        return next_token

    wait = AsyncMock(side_effect=[None, StopRefreshLoop()])
    with (
        patch.object(exporter, "rotate_token", side_effect=rotate) as rotate_mock,
        patch("jumpstarter.exporter.exporter.sleep", wait),
        patch("jumpstarter.exporter.token_refresh.time.time", return_value=now),
        pytest.raises(StopRefreshLoop),
    ):
        await exporter._token_refresh_loop()
    assert rotate_mock.await_count == 2
    assert exporter.token == next_token
    assert [call.args[0] for call in wait.await_args_list] == [retry_delay, 3600.0]


@pytest.mark.anyio
@pytest.mark.parametrize("remaining", [10.0, 0.0])
@pytest.mark.parametrize("error", [
    # The controller reports an expired token as UNKNOWN, not UNAUTHENTICATED.
    pytest.param(FakeRpcError(grpc.StatusCode.UNKNOWN, "oidc: token is expired"), id="unknown"),
    pytest.param(FakeRpcError(grpc.StatusCode.UNIMPLEMENTED), id="unimplemented"),
    pytest.param(FakeRpcError(grpc.StatusCode.PERMISSION_DENIED), id="denied"),
    pytest.param(RuntimeError("unexpected"), id="unexpected"),
])
async def test_refresh_never_rotates_expired_token(error, remaining):
    now = 1_000_000.0
    expiry = now + remaining
    exporter = _exporter_with_token(_make_jwt({"iat": expiry - 100, "exp": expiry}))
    attempted_at = []

    with patch("jumpstarter.exporter.token_refresh.time.time", return_value=now) as clock:

        async def rotate():
            attempted_at.append(clock.return_value)
            clock.return_value += 1
            raise error

        async def wait(seconds):
            if len(attempted_at) > 50:
                raise StopRefreshLoop()
            clock.return_value += seconds

        with (
            patch.object(exporter, "rotate_token", side_effect=rotate),
            patch("jumpstarter.exporter.exporter.sleep", side_effect=wait),
        ):
            await exporter._token_refresh_loop()
    assert all(at < expiry for at in attempted_at)


@pytest.mark.anyio
async def test_refresh_stops_when_controller_rejects_credential():
    now = time.time()
    exporter = _exporter_with_token(_make_jwt({"iat": now - 100, "exp": now + 10}))
    with (
        patch.object(exporter, "rotate_token", side_effect=FakeRpcError(grpc.StatusCode.UNAUTHENTICATED)) as rotate,
        patch("jumpstarter.exporter.exporter.sleep", new_callable=AsyncMock) as wait,
    ):
        await exporter._token_refresh_loop()
    rotate.assert_awaited_once()
    wait.assert_not_awaited()


@pytest.mark.anyio
async def test_refresh_rechecks_wall_clock_after_capped_sleep():
    now = 1_000_000.0
    lifetime = 365 * 86400
    exporter = _exporter_with_token(_make_jwt({"iat": now, "exp": now + lifetime}))
    resumed_at = now + 360 * 86400
    next_token = _make_jwt({"iat": resumed_at, "exp": resumed_at + lifetime})
    waits = []

    with patch("jumpstarter.exporter.token_refresh.time.time", return_value=now) as clock:

        async def wait(seconds):
            waits.append(seconds)
            if len(waits) == 1:
                clock.return_value = resumed_at
            else:
                raise StopRefreshLoop()

        async def rotate():
            exporter.token = next_token
            return next_token

        with (
            patch.object(exporter, "rotate_token", side_effect=rotate) as rotate_mock,
            patch("jumpstarter.exporter.exporter.sleep", side_effect=wait),
            pytest.raises(StopRefreshLoop),
        ):
            await exporter._token_refresh_loop()
    assert waits == [3600.0, 3600.0]
    rotate_mock.assert_awaited_once()
    assert exporter.token == next_token


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token",
    [
        _make_jwt({"iss": "https://idp.example.com", "sub": "user@example.com", "exp": 1}),
        _make_jwt({"iss": "", "exp": 1}),
        _make_jwt({"exp": None}),
        "not-a-jwt",
        "",
    ],
)
async def test_refresh_skips_external_and_non_expiring_credentials(token):
    exporter = _exporter_with_token(token)
    with patch.object(exporter, "rotate_token", new_callable=AsyncMock) as rotate_mock:
        with anyio.fail_after(1):
            await exporter._token_refresh_loop()
    rotate_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_rotation_passes_current_token_to_factory_without_callback():
    exporter = _exporter_with_token("original-token")
    controller = AsyncMock()
    controller.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="new-token")
    with (
        patch.object(exporter, "channel_factory", new_callable=AsyncMock) as channel_factory,
        patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=controller),
    ):
        await exporter.rotate_token()
        async with exporter._controller_stub():
            pass
    assert [call.args[0] for call in channel_factory.await_args_list] == ["original-token", "new-token"]


@pytest.mark.anyio
async def test_config_on_token_rotated_saves_to_path(saved_rotation_config):
    loaded = saved_rotation_config
    assert loaded.token == "initial-token"

    event_loop_thread = threading.get_ident()
    save_threads = []
    original_save = ExporterConfigV1Alpha1._save_rotated_token

    def save(token):
        save_threads.append(threading.get_ident())
        return original_save(loaded, token)

    with patch.object(ExporterConfigV1Alpha1, "_save_rotated_token", side_effect=save):
        async with loaded.create_exporter() as exporter:
            assert exporter.on_token_rotated is not None
            await exporter.on_token_rotated("new-saved-token")
    assert len(save_threads) == 1
    assert save_threads[0] != event_loop_thread

    assert loaded.token == "new-saved-token"
    reloaded = ExporterConfigV1Alpha1.load_path(loaded.path)
    assert reloaded.token == "new-saved-token"


@pytest.mark.anyio
async def test_config_token_save_does_not_delay_cancellation(saved_rotation_config):
    loaded = saved_rotation_config
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    cancelled = anyio.Event()
    scopes = []

    def blocked_save(token):
        started.set()
        try:
            assert release.wait(timeout=5)
        finally:
            finished.set()

    async with loaded.create_exporter() as exporter:

        async def rotate():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                try:
                    await exporter.on_token_rotated("new-token")
                finally:
                    cancelled.set()

        with patch.object(ExporterConfigV1Alpha1, "_save_rotated_token", side_effect=blocked_save):
            try:
                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(rotate)
                    with anyio.fail_after(2):
                        while not started.is_set():
                            await anyio.sleep(0.01)
                        scopes[0].cancel()
                        await cancelled.wait()
                    assert not release.is_set()
            finally:
                release.set()
                with anyio.fail_after(2):
                    while not finished.is_set():
                        await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_token_persistence_preserves_edits_and_symlink_but_restricts_mode(tmp_path, caplog):
    target = tmp_path / "managed.yaml"
    cfg = ExporterConfigV1Alpha1(
        metadata=ObjectMeta(name="my-exporter", namespace="default"),
        endpoint="localhost:8082",
        token="initial-token",
        tls=TLSConfigV1Alpha1(insecure=True),
    )
    ExporterConfigV1Alpha1.save(cfg, path=str(target))
    link = tmp_path / "exporter.yaml"
    link.symlink_to(target.name)
    loaded = ExporterConfigV1Alpha1.load_path(link)
    data = yaml.safe_load(target.read_text())
    data["description"] = "edited while running"
    data["export"] = {"new-driver": {"type": "some.Driver"}}
    data["custom-field"] = "preserved"
    target.write_text(yaml.safe_dump(data))
    target.chmod(0o644)

    with caplog.at_level(logging.WARNING, logger="jumpstarter.config.exporter"):
        async with loaded.create_exporter() as exporter:
            await exporter.on_token_rotated("new-token")

    assert link.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert f"Exporter config {target.resolve()} was readable by other users (mode 644)" in caplog.text
    data["token"] = "new-token"
    assert yaml.safe_load(target.read_text()) == data
    assert loaded.token == "new-token"


@pytest.mark.anyio
async def test_unsupported_rotation_warns_once(caplog):
    now = time.time()
    exporter = _exporter_with_token(_make_jwt({"iat": now - 1000, "exp": now + 100}))
    wait = AsyncMock(side_effect=[None, None, StopRefreshLoop()])
    with (
        caplog.at_level(logging.DEBUG),
        patch.object(exporter, "rotate_token", side_effect=FakeRpcError(grpc.StatusCode.UNIMPLEMENTED)),
        patch("jumpstarter.exporter.exporter.sleep", wait),
        pytest.raises(StopRefreshLoop),
    ):
        await exporter._token_refresh_loop()
    records = [record for record in caplog.records if "does not support token rotation" in record.message]
    assert [record.levelno for record in records] == [logging.WARNING, logging.DEBUG, logging.DEBUG]


@pytest.mark.anyio
async def test_control_plane_starts_token_refresh_loop():
    exporter = _exporter_with_token(_make_jwt({"iat": 0, "exp": 1}))
    refresh_started = anyio.Event()

    async def refresh():
        refresh_started.set()
        await anyio.sleep_forever()

    async def status_stream(*args, **kwargs):
        await anyio.sleep_forever()

    send, receive = anyio.create_memory_object_stream()
    with (
        patch.object(exporter, "_token_refresh_loop", side_effect=refresh),
        patch.object(exporter, "_retry_stream", side_effect=status_stream),
    ):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(exporter._run_control_plane, send, receive)
            with anyio.fail_after(2):
                await refresh_started.wait()
            tasks.cancel_scope.cancel()


@pytest.fixture
def saved_rotation_config(tmp_path):
    config = ExporterConfigV1Alpha1(
        metadata=ObjectMeta(name="my-exporter", namespace="default"),
        endpoint="localhost:8082",
        token="initial-token",
        tls=TLSConfigV1Alpha1(insecure=True),
    )
    path = tmp_path / "exporter.yaml"
    ExporterConfigV1Alpha1.save(config, path=str(path))
    return ExporterConfigV1Alpha1.load_path(path)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [BrokenPipeError("parent exited"), RuntimeError("notification failed")])
async def test_notification_failure_still_persists_token(saved_rotation_config, caplog, error):
    notify = MagicMock(side_effect=error)
    with caplog.at_level(logging.ERROR, logger="jumpstarter.config.exporter"):
        async with saved_rotation_config.create_exporter(on_token_rotated=notify) as exporter:
            await exporter.on_token_rotated("replacement-token")
    notify.assert_called_once_with("replacement-token")
    assert "Could not notify exporter supervisor" in caplog.text
    assert saved_rotation_config.token == "replacement-token"
    assert ExporterConfigV1Alpha1.load_path(saved_rotation_config.path).token == "replacement-token"


@pytest.mark.anyio
@pytest.mark.parametrize("error,level", [
    (OSError(errno.EROFS, "read-only filesystem"), logging.INFO),
    (PermissionError(errno.EACCES, "permission denied"), logging.WARNING),
])
async def test_token_persistence_failure_log_level(saved_rotation_config, caplog, error, level):
    with (
        caplog.at_level(logging.INFO),
        patch.object(ExporterConfigV1Alpha1, "_save_rotated_token", side_effect=error),
    ):
        async with saved_rotation_config.create_exporter() as exporter:
            await exporter.on_token_rotated("replacement-token")
    records = [record for record in caplog.records if "Could not save rotated token" in record.message]
    assert [record.levelno for record in records] == [level]
    assert saved_rotation_config.token == "replacement-token"
