"""Client-side telemetry: discover the hub and push jmp logs via PushLogs."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace

import grpc
from anyio import create_task_group
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc, telemetry_pb2_grpc

from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.common.telemetry_token import TelemetryCredential
from jumpstarter.config.env import JMP_GRPC_INSECURE, JUMPSTARTER_GRPC_INSECURE
from jumpstarter.exporter.telemetry import TelemetryLogHandler

logger = logging.getLogger(__name__)

_RPC_TIMEOUT = 10.0


def _severity_to_level(severity: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get((severity or "").lower(), logging.INFO)


@dataclass
class ClientTelemetry:
    """Discovered telemetry endpoint. The channel opens in ``run``, not at attach time.

    ``tls.insecure`` discovers the server certificate by dialing the telemetry
    endpoint. Doing that before the lease would stall or fail ``jmp shell``
    when the telemetry process is down. PushLogs stays best-effort: one dial
    attempt, then the session continues without a handler.
    """

    endpoint: str
    certificate: str
    insecure: bool
    namespace: str
    min_severity: str
    token: TelemetryCredential
    discover: Callable[[], Awaitable[jumpstarter_pb2.GetServiceEndpointsResponse]]
    identity_metadata: tuple[tuple[str, str], ...]
    handler: TelemetryLogHandler | None = None
    channel: grpc.aio.Channel | None = None
    _closed: bool = False

    async def run(self) -> None:
        """Open the channel once, then flush and renew the token until cancelled."""
        if not await self._open():
            return
        handler = self.handler
        if handler is None:
            return
        async with create_task_group() as tg:
            tg.start_soon(self.token.renew, self.discover)
            await handler.flush_loop()

    async def _open(self) -> bool:
        if self._closed or self.handler is not None:
            return self.handler is not None
        try:
            channel = await _telemetry_channel(self.endpoint, self.certificate, self.insecure)
        except Exception as exc:  # noqa: BLE001
            # Same best-effort boundary as GetServiceEndpoints. Cancellation is
            # a BaseException and still propagates.
            logger.debug("telemetry channel unavailable: %s", exc)
            return False
        if self._closed:
            await _close_channel(channel)
            return False
        self.channel = channel
        handler = TelemetryLogHandler(
            telemetry_pb2_grpc.TelemetryServiceStub(channel),
            namespace=self.namespace,
            token=self.token,
            component="cli",
            identity_metadata=self.identity_metadata,
        )
        handler.setLevel(_severity_to_level(self.min_severity))
        logging.getLogger().addHandler(handler)
        self.handler = handler
        logger.info("Telemetry log handler attached (component=cli)")
        return True

    async def aclose(self) -> None:
        self._closed = True
        handler = self.handler
        channel = self.channel
        self.handler = None
        self.channel = None
        if handler is not None:
            try:
                await handler.close_async()
            except Exception:
                logger.debug("telemetry handler close failed", exc_info=True)
            logging.getLogger().removeHandler(handler)
        await _close_channel(channel)


async def _telemetry_channel(endpoint: str, certificate: str, insecure: bool) -> grpc.aio.Channel:
    grpc_insecure = insecure or os.getenv(JMP_GRPC_INSECURE) == "1" or os.getenv(JUMPSTARTER_GRPC_INSECURE) == "1"
    # tls.insecure still uses TLS. The controller channel discovers the server
    # certificate without verification; a plaintext channel cannot complete a
    # handshake with the telemetry server, which always requires TLS.
    if grpc_insecure and not certificate:
        credentials = await ssl_channel_credentials(endpoint, SimpleNamespace(insecure=True, ca=""))
        return aio_secure_channel(endpoint, credentials, None)
    if certificate:
        return grpc.aio.secure_channel(
            endpoint,
            grpc.ssl_channel_credentials(root_certificates=certificate.encode()),
        )
    return grpc.aio.secure_channel(endpoint, grpc.ssl_channel_credentials())


async def _close_channel(channel: grpc.aio.Channel | None) -> None:
    if channel is None:
        return
    try:
        await channel.close()
    except Exception:
        logger.debug("telemetry channel close failed", exc_info=True)


async def attach_client_telemetry(config) -> ClientTelemetry | None:
    """Best-effort GetServiceEndpoints + TelemetryLogHandler for jmp.

    Human-facing stdout handlers are left in place. The PushLogs handler is
    attached from ``ClientTelemetry.run`` after the telemetry channel opens,
    so shell startup does not wait on the telemetry process.
    """
    async def discover() -> jumpstarter_pb2.GetServiceEndpointsResponse:
        # A new channel carries the config's current token.
        async with await config.channel() as channel:
            return await jumpstarter_pb2_grpc.ControllerServiceStub(channel).GetServiceEndpoints(
                jumpstarter_pb2.GetServiceEndpointsRequest(),
                timeout=_RPC_TIMEOUT,
            )

    try:
        resp = await discover()
    except Exception as exc:  # noqa: BLE001
        logger.debug("GetServiceEndpoints unavailable: %s", exc)
        return None

    if not resp.telemetry_endpoints:
        logger.debug("No telemetry endpoint configured, skipping client telemetry")
        return None

    ep = resp.telemetry_endpoints[0]
    tls = getattr(config, "tls", None)
    namespace = ""
    client_name = ""
    metadata = getattr(config, "metadata", None)
    if metadata is not None:
        namespace = getattr(metadata, "namespace", "") or ""
        client_name = getattr(metadata, "name", "") or ""
    identity_metadata = [
        ("jumpstarter-kind", "Client"),
        ("jumpstarter-namespace", namespace),
    ]
    if client_name:
        identity_metadata.append(("jumpstarter-name", client_name))

    return ClientTelemetry(
        endpoint=ep.endpoint,
        certificate=ep.certificate or "",
        insecure=bool(getattr(tls, "insecure", False)),
        namespace=namespace,
        min_severity=ep.min_severity,
        token=TelemetryCredential(ep),
        discover=discover,
        identity_metadata=tuple(identity_metadata),
    )
