"""Exporter MetricsStream client — reverse-scrape snapshots for jumpstarter-telemetry.

Does not replace the local HTTP GET /metrics loopback bind (CLI :0); reverse-scrape
is an additional path using the same generate_latest() bytes.
"""

from __future__ import annotations

import logging
from typing import Any

from anyio import sleep
from google.protobuf.timestamp_pb2 import Timestamp
from jumpstarter_protocol import telemetry_pb2, telemetry_pb2_grpc

from jumpstarter.metrics import MetricsRegistry, get_registry

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

    def _metrics_text(self) -> bytes:
        reg = self._registry if self._registry is not None else get_registry()
        return reg.generate_latest()

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
            except Exception as exc:
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
            ts = Timestamp()
            ts.GetCurrentTime()
            await call.write(
                telemetry_pb2.MetricsStreamRequest(
                    scrape_response=telemetry_pb2.MetricsScrapeResponse(
                        metrics_text=self._metrics_text(),
                        timestamp=ts,
                    ),
                )
            )
