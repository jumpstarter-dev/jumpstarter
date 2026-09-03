"""Exporter MetricsStream client — reverse-scrape snapshots for jumpstarter-telemetry.

Does not replace the local HTTP GET /metrics loopback bind (CLI :0). Reverse-scrape
is an additional path: ``families`` is the payload the hub merges, and ``metrics_text``
is the same ``generate_latest()`` bytes as a local scrape.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import grpc
from anyio import sleep
from jumpstarter_protocol import telemetry_pb2, telemetry_pb2_grpc

from jumpstarter.metrics import MetricsRegistry, get_registry, scrape_response_from_registry

logger = logging.getLogger(__name__)

_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0
# Auth and identity rejections are not "telemetry is down". Surface them once
# per distinct code; repeats stay at debug so a bad token cannot flood logs.
_NON_TRANSIENT_STREAM_CODES = frozenset({
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.UNAUTHENTICATED,
})


def _grpc_status(exc: Exception) -> grpc.StatusCode | None:
    if isinstance(exc, grpc.aio.AioRpcError):
        return exc.code()
    return None


class MetricsStreamClient:
    """Long-lived MetricsStream: register, answer scrapes, reconnect with backoff."""

    def __init__(
        self,
        stub: telemetry_pb2_grpc.TelemetryServiceStub,
        *,
        identity: str,
        token: str | Callable[[], str] = "",
        registry: MetricsRegistry | None = None,
        backoff_base: float = _BACKOFF_BASE,
        backoff_cap: float = _BACKOFF_CAP,
    ) -> None:
        self.identity = identity
        self.token = token
        self._stub = stub
        self._registry = registry
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap

    def _registry_or_default(self) -> MetricsRegistry:
        return self._registry if self._registry is not None else get_registry()

    def _call_kwargs(self) -> dict[str, Any]:
        # Resolve per session so a reconnect uses the exporter's current token.
        token = self.token() if callable(self.token) else self.token
        if token:
            return {"metadata": [("authorization", f"Bearer {token}")]}
        return {}

    async def run(self) -> None:
        """Reconnect forever until cancelled. Local counters are never reset."""
        delay = self._backoff_base
        reported: grpc.StatusCode | None = None
        while True:
            try:
                await self._session()
                delay = self._backoff_base
                if reported is not None:
                    logger.info("MetricsStream connected")
                    reported = None
            except Exception as exc:  # noqa: BLE001 — reconnect on any stream failure; counters stay local.
                code = _grpc_status(exc)
                if code in _NON_TRANSIENT_STREAM_CODES and code != reported:
                    logger.warning("MetricsStream rejected (%s); retrying in %ss", code.name, delay)
                    reported = code
                else:
                    logger.debug("MetricsStream disconnected: %s; retry in %ss", exc, delay)
                    if code not in _NON_TRANSIENT_STREAM_CODES:
                        reported = None
                await sleep(delay)
                delay = min(delay * 2, self._backoff_cap)
                continue
            await sleep(delay)

    async def _session(self) -> None:
        call = self._stub.MetricsStream(**self._call_kwargs())
        try:
            await call.write(
                telemetry_pb2.MetricsStreamRequest(
                    register=telemetry_pb2.MetricsRegister(identity=self.identity),
                )
            )
            async for resp in call:
                if resp.WhichOneof("msg") != "scrape_request":
                    continue
                await call.write(
                    telemetry_pb2.MetricsStreamRequest(
                        scrape_response=scrape_response_from_registry(self._registry_or_default()),
                    )
                )
        finally:
            # A local failure, such as building the scrape response, used to
            # leave the server-side RPC open until the channel closed.
            call.cancel()
