"""Tests for jmp client telemetry: GetServiceEndpoints + PushLogs (JEP-0013 Phase 3)."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from jumpstarter.client.telemetry import attach_client_telemetry
from jumpstarter.common.exceptions import CertificateDiscoveryError

pytestmark = pytest.mark.anyio


def make_endpoint(endpoint="telemetry:19093", certificate="", min_severity="info"):
    ep = MagicMock()
    ep.endpoint = endpoint
    ep.certificate = certificate
    ep.min_severity = min_severity
    return ep


def make_config(*, token="tok", namespace="jumpstarter", insecure=False):
    tls = SimpleNamespace(insecure=insecure)
    metadata = SimpleNamespace(namespace=namespace)

    config = SimpleNamespace(
        token=token,
        tls=tls,
        metadata=metadata,
        grpcOptions={},
    )

    async def channel():
        return MagicMock(name="controller-channel")

    config.channel = channel
    return config


async def test_attach_skips_when_no_endpoints():
    resp = MagicMock()
    resp.telemetry_endpoints = []
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=resp)

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub),
        patch("jumpstarter.client.telemetry.logging.getLogger") as get_logger,
    ):
        session = await attach_client_telemetry(make_config())

    assert session is None
    get_logger.return_value.addHandler.assert_not_called()


async def test_attach_swallows_rpc_errors():
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(
        side_effect=grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNIMPLEMENTED,
            initial_metadata=None,
            trailing_metadata=None,
        )
    )

    with patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub):
        session = await attach_client_telemetry(make_config())

    assert session is None


async def test_attach_adds_cli_handler_without_replacing_stdout():
    resp = MagicMock()
    resp.telemetry_endpoints = [make_endpoint(min_severity="warning")]
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=resp)

    root = logging.getLogger()
    before = list(root.handlers)

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub),
        patch(
            "jumpstarter.client.telemetry.ssl_channel_credentials",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("jumpstarter.client.telemetry.aio_secure_channel") as insecure,
        patch("jumpstarter.client.telemetry.telemetry_pb2_grpc.TelemetryServiceStub") as tel_stub,
    ):
        insecure.return_value = MagicMock()
        tel_stub.return_value = MagicMock()
        session = await attach_client_telemetry(make_config(insecure=True))
        assert session is not None
        assert await session._open()

    try:
        assert session is not None
        handler = session.handler
        assert handler is not None
        assert handler.component == "cli"
        assert handler.level == logging.WARNING
        assert handler in root.handlers
        for h in before:
            assert h in root.handlers
    finally:
        if session is not None:
            await session.aclose()


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="jmp.shell",
        level=logging.INFO,
        pathname="shell.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


async def test_attach_sends_token_current_at_flush():
    resp = MagicMock()
    resp.telemetry_endpoints = [make_endpoint()]
    controller = AsyncMock()
    controller.GetServiceEndpoints = AsyncMock(return_value=resp)
    push = AsyncMock()
    tel = MagicMock()
    tel.PushLogs = push
    config = make_config(token="old-token", insecure=True)

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=controller),
        patch(
            "jumpstarter.client.telemetry.ssl_channel_credentials",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("jumpstarter.client.telemetry.aio_secure_channel") as insecure,
        patch("jumpstarter.client.telemetry.telemetry_pb2_grpc.TelemetryServiceStub", return_value=tel),
    ):
        insecure.return_value = MagicMock()
        session = await attach_client_telemetry(config)
        assert session is not None
        assert await session._open()

    try:
        handler = session.handler
        assert handler is not None
        handler.emit(_record("before refresh"))
        await handler._flush()
        config.token = "new-token"
        handler.emit(_record("after refresh"))
        await handler._flush()
    finally:
        await session.aclose()

    assert push.await_count == 2
    identity = [
        ("jumpstarter-kind", "Client"),
        ("jumpstarter-namespace", "jumpstarter"),
    ]
    assert push.await_args_list[0].kwargs["metadata"] == [
        *identity,
        ("authorization", "Bearer old-token"),
    ]
    assert push.await_args_list[1].kwargs["metadata"] == [
        *identity,
        ("authorization", "Bearer new-token"),
    ]


async def test_attach_does_not_dial_telemetry():
    """Shell startup only discovers the endpoint. The telemetry dial is deferred."""
    resp = MagicMock()
    resp.telemetry_endpoints = [make_endpoint()]
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=resp)
    dial = AsyncMock(side_effect=AssertionError("dialled during attach"))

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub),
        patch("jumpstarter.client.telemetry.ssl_channel_credentials", new=dial),
    ):
        session = await attach_client_telemetry(make_config(insecure=True))

    assert session is not None
    dial.assert_not_called()
    assert session.handler is None


async def test_open_skips_handler_when_telemetry_is_down(caplog):
    """A down telemetry process drops client logs and leaves the shell running."""
    resp = MagicMock()
    resp.telemetry_endpoints = [make_endpoint()]
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=resp)
    root = logging.getLogger()
    before = list(root.handlers)

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub),
        patch(
            "jumpstarter.client.telemetry.ssl_channel_credentials",
            new=AsyncMock(side_effect=CertificateDiscoveryError("telemetry down")),
        ),
        caplog.at_level(logging.DEBUG, logger="jumpstarter.client.telemetry"),
    ):
        session = await attach_client_telemetry(make_config(insecure=True))
        assert session is not None
        opened = await session._open()

    assert opened is False
    assert session.handler is None
    assert session.channel is None
    assert list(root.handlers) == before
    assert "telemetry channel unavailable" in caplog.text


async def test_aclose_before_open_skips_the_dial():
    resp = MagicMock()
    resp.telemetry_endpoints = [make_endpoint()]
    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=resp)
    dial = AsyncMock(side_effect=AssertionError("dialled after close"))

    with (
        patch("jumpstarter.client.telemetry.jumpstarter_pb2_grpc.ControllerServiceStub", return_value=stub),
        patch("jumpstarter.client.telemetry.ssl_channel_credentials", new=dial),
    ):
        session = await attach_client_telemetry(make_config(insecure=True))
        assert session is not None
        await session.aclose()
        assert await session._open() is False

    dial.assert_not_called()
    assert session.handler is None
