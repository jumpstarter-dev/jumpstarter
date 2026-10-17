"""Exporter MetricsStream client — reverse-scrape snapshots for jumpstarter-telemetry.

Does not replace the local HTTP GET /metrics loopback bind (CLI :0). Reverse-scrape
is an additional path: ``families`` is the payload the hub merges, and ``metrics_text``
is the same ``generate_latest()`` bytes as a local scrape.
"""

from __future__ import annotations

import logging
from typing import Any

from anyio import sleep
from jumpstarter_protocol import telemetry_pb2, telemetry_pb2_grpc

from jumpstarter.metrics import MetricsRegistry, get_registry, scrape_response_from_registry

logger = logging.getLogger(__name__)

_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0


class MetricsStreamClient:
    """Long-lived MetricsStream: register, answer scrapes, reconnect with backoff."""

    def __init__(
        self,
        stub: telemetry_pb2_grpc.TelemetryServiceStub,
        *,
        identity: str,
        token: str = "",
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
        if self.token:
            return {"metadata": [("authorization", f"Bearer {self.token}")]}
        return {}

    async def run(self) -> None:
        """Reconnect forever until cancelled. Local counters are never reset."""
        delay = self._backoff_base
        while True:
            try:
                await self._session()
                delay = self._backoff_base
            except Exception as exc:  # noqa: BLE001 — reconnect on any stream failure; counters stay local.
                logger.debug("MetricsStream disconnected: %s; retry in %ss", exc, delay)
                await sleep(delay)
                delay = min(delay * 2, self._backoff_cap)
                continue
            await sleep(delay)

    async def _session(self) -> None:
        call = self._stub.MetricsStream(**self._call_kwargs())
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
