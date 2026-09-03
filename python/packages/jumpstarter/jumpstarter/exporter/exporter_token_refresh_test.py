import base64
import json
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import grpc
import pytest
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.exporter import Exporter
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
async def test_exporter_rotate_token_success():
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    mock_channel.close = AsyncMock()

    mock_ctrl = AsyncMock()
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    async def channel_factory(token):
        return mock_channel

    on_rotated = AsyncMock()
    mock_telemetry_stub = AsyncMock()
    telemetry_handler = TelemetryLogHandler(mock_telemetry_stub, token="old-token")

    exporter = Exporter(
        exporter_name="test-exporter",
        token="old-token",
        channel_factory=channel_factory,
        device_factory=lambda: MagicMock(),
        on_token_rotated=on_rotated,
    )
    exporter._telemetry_handler = telemetry_handler

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        token = await exporter.rotate_token()

    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    telemetry_handler.emit(logging.LogRecord("test", logging.INFO, __file__, 1, "test message", (), None))
    await telemetry_handler._flush()
    assert mock_telemetry_stub.PushLogs.call_args.kwargs["metadata"] == [
        ("authorization", "Bearer rotated-token-value")
    ]
    on_rotated.assert_awaited_once_with("rotated-token-value")
    mock_ctrl.RotateToken.assert_awaited_once()


@pytest.mark.anyio
async def test_exporter_rotate_token_sync_callback():
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    mock_channel.close = AsyncMock()

    mock_ctrl = AsyncMock()
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    async def channel_factory(token):
        return mock_channel

    recorded_tokens = []

    def sync_on_rotated(token: str) -> None:
        recorded_tokens.append(token)

    exporter = Exporter(
        exporter_name="test-exporter",
        token="old-token",
        channel_factory=channel_factory,
        device_factory=lambda: MagicMock(),
        on_token_rotated=sync_on_rotated,
    )

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        token = await exporter.rotate_token()

    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    assert recorded_tokens == ["rotated-token-value"]
    mock_ctrl.RotateToken.assert_awaited_once()


@pytest.mark.anyio
async def test_exporter_rotate_token_callback_failure():
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    mock_channel.close = AsyncMock()

    mock_ctrl = AsyncMock()
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token="rotated-token-value")

    async def channel_factory(token):
        return mock_channel

    on_rotated = AsyncMock(side_effect=RuntimeError("disk full or save failed"))

    exporter = Exporter(
        exporter_name="test-exporter",
        token="old-token",
        channel_factory=channel_factory,
        device_factory=lambda: MagicMock(),
        on_token_rotated=on_rotated,
    )

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        token = await exporter.rotate_token()

    # Normal rotation result is preserved and token is updated
    assert token == "rotated-token-value"
    assert exporter.token == "rotated-token-value"
    on_rotated.assert_awaited_once_with("rotated-token-value")
    mock_ctrl.RotateToken.assert_awaited_once()


@pytest.mark.anyio
async def test_exporter_token_refresh_loop_near_expiry():
    # Token expiring in 1 second, with lead time 10s -> refresh sleep is 0s (immediate)
    now = time.time()
    token = _make_jwt({"iat": now - 100, "exp": now + 1})

    mock_channel = MagicMock(spec=grpc.aio.Channel)
    mock_channel.close = AsyncMock()

    mock_ctrl = AsyncMock()
    # Next token valid for 1000s so it doesn't loop infinitely
    next_token = _make_jwt({"iat": now, "exp": now + 100000})
    mock_ctrl.RotateToken.return_value = jumpstarter_pb2.RotateTokenResponse(token=next_token)

    async def channel_factory(token):
        return mock_channel

    exporter = Exporter(
        exporter_name="test-exporter",
        token=token,
        channel_factory=channel_factory,
        device_factory=lambda: MagicMock(),
        token_refresh_lead_time=10.0,
    )

    with patch("jumpstarter.exporter.exporter.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=mock_ctrl):
        with anyio.move_on_after(0.5) as cancel_scope:
            await exporter._token_refresh_loop()

    assert cancel_scope.cancel_called
    assert exporter.token == next_token
    mock_ctrl.RotateToken.assert_awaited_once()


class StopRefreshLoop(Exception):
    pass


def _exporter_with_token(token: str) -> Exporter:
    channel = MagicMock(spec=grpc.aio.Channel)
    channel.close = AsyncMock()
    return Exporter(
        exporter_name="test-exporter",
        token=token,
        channel_factory=AsyncMock(return_value=channel),
        device_factory=MagicMock(),
    )


@pytest.mark.anyio
async def test_exporter_token_refresh_loop_recovers_after_controller_upgrade():
    now = time.time()
    exporter = _exporter_with_token(_make_jwt({"iat": now - 100, "exp": now + 1}))
    next_token = _make_jwt({"iat": now, "exp": now + 100000})
    attempts = 0

    async def rotate():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FakeRpcError(grpc.StatusCode.UNIMPLEMENTED)
        exporter.token = next_token
        return next_token

    wait = AsyncMock(side_effect=[None, StopRefreshLoop()])
    with (
        patch.object(exporter, "rotate_token", side_effect=rotate) as rotate_mock,
        patch("jumpstarter.exporter.exporter.sleep", wait),
        pytest.raises(StopRefreshLoop),
    ):
        await exporter._token_refresh_loop()
    assert rotate_mock.await_count == 2
    assert exporter.token == next_token
    assert [call.args[0] for call in wait.await_args_list] == [3600.0, 3600.0]


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
@pytest.mark.parametrize("claims", [
    {"iss": "https://idp.example.com", "sub": "user@example.com", "exp": 1},
    {"iss": "", "exp": 1},
    {"exp": None},
])
async def test_refresh_skips_external_and_non_expiring_credentials(claims):
    exporter = _exporter_with_token(_make_jwt(claims))
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
async def test_exporter_token_refresh_loop_standalone_and_no_token():
    exporter_standalone = Exporter(
        exporter_name="test-exporter",
        token="dummy-token",
        channel_factory=AsyncMock(),
        device_factory=lambda: MagicMock(),
    )
    exporter_standalone._standalone = True
    # Should return immediately
    with anyio.fail_after(1.0):
        await exporter_standalone._token_refresh_loop()

    exporter_no_token = Exporter(
        exporter_name="test-exporter",
        token="",
        channel_factory=AsyncMock(),
        device_factory=lambda: MagicMock(),
    )
    # Should return immediately
    with anyio.fail_after(1.0):
        await exporter_no_token._token_refresh_loop()


@pytest.mark.anyio
async def test_config_on_token_rotated_saves_to_path(tmp_path):
    config_file = tmp_path / "exporter.yaml"
    cfg = ExporterConfigV1Alpha1(
        metadata=ObjectMeta(name="my-exporter", namespace="default"),
        endpoint="localhost:8082",
        token="initial-token",
        tls=TLSConfigV1Alpha1(insecure=True),
    )
    ExporterConfigV1Alpha1.save(cfg, path=str(config_file))
    loaded = ExporterConfigV1Alpha1.load_path(config_file)
    assert loaded.token == "initial-token"

    event_loop_thread = threading.get_ident()
    save_threads = []
    original_save = ExporterConfigV1Alpha1.save

    def save(config, path=None):
        save_threads.append(threading.get_ident())
        return original_save(config, path=path)

    with patch.object(ExporterConfigV1Alpha1, "save", side_effect=save):
        async with loaded.create_exporter() as exporter:
            assert exporter.on_token_rotated is not None
            await exporter.on_token_rotated("new-saved-token")
    assert len(save_threads) == 1
    assert save_threads[0] != event_loop_thread

    assert loaded.token == "new-saved-token"
    # Verify persisted to disk
    reloaded = ExporterConfigV1Alpha1.load_path(config_file)
    assert reloaded.token == "new-saved-token"
