"""Bearer token for TelemetryService calls."""

import logging
import time
from collections.abc import Awaitable, Callable

import grpc
from anyio import sleep
from jumpstarter_protocol import jumpstarter_pb2

logger = logging.getLogger(__name__)

# Renew halfway through the remaining lifetime, but no more often than this,
# which is also the retry interval after a failed renewal.
_MIN_RENEWAL_DELAY = 30.0


class TelemetryCredential:
    """Telemetry token from GetServiceEndpoints, renewed before it expires.

    The controller issues it after checking the caller against its Exporter
    or Client, which telemetry cannot look up itself. Telemetry accepts no
    other credential.

    Calling the instance returns the current token.
    """

    _token: str
    _expiry: int

    def __init__(self, endpoint: jumpstarter_pb2.TelemetryEndpoint) -> None:
        self.update(endpoint)

    def update(self, endpoint: jumpstarter_pb2.TelemetryEndpoint) -> None:
        self._token = endpoint.token
        self._expiry = endpoint.token_expiry.ToSeconds()

    def __call__(self) -> str:
        return self._token

    async def renew(self, discover: Callable[[], Awaitable[jumpstarter_pb2.GetServiceEndpointsResponse]]) -> None:
        """Renew the token before it expires, until cancelled.

        Returns when the controller no longer offers telemetry. A rejected
        credential is retried like any other failure: jmp shell can replace an
        expired one when the user runs jmp login inside it.
        """
        while True:
            await sleep(max((self._expiry - time.time()) / 2, _MIN_RENEWAL_DELAY))
            try:
                resp = await discover()
            except grpc.aio.AioRpcError as e:
                logger.debug("Telemetry token renewal failed: %s", e.code())
                continue
            except Exception as e:  # noqa: BLE001 - telemetry is best-effort
                logger.debug("Telemetry token renewal failed: %s", e)
                continue
            if not resp.telemetry_endpoints:
                return
            self.update(resp.telemetry_endpoints[0])
