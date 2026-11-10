import time
from unittest.mock import AsyncMock, patch

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common.telemetry_token import TelemetryCredential

pytestmark = pytest.mark.anyio


def endpoint(token: str, expires_in: float) -> jumpstarter_pb2.TelemetryEndpoint:
    ep = jumpstarter_pb2.TelemetryEndpoint(endpoint="telemetry:9093", token=token)
    ep.token_expiry.CopyFrom(Timestamp(seconds=int(time.time() + expires_in)))
    return ep


class _Stop(Exception):
    pass


def stop_on_second_sleep(delays: list[float]):
    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 2:
            raise _Stop

    return sleep


async def test_renew_replaces_token_halfway_to_expiry():
    cred = TelemetryCredential(endpoint("first", 3600))
    renewed = jumpstarter_pb2.GetServiceEndpointsResponse(telemetry_endpoints=[endpoint("second", 600)])
    delays: list[float] = []

    with (
        patch("jumpstarter.common.telemetry_token.sleep", stop_on_second_sleep(delays)),
        pytest.raises(_Stop),
    ):
        await cred.renew(AsyncMock(return_value=renewed))

    assert cred() == "second"
    assert delays[0] == pytest.approx(1800, abs=2)
    assert delays[1] == pytest.approx(300, abs=2)


def rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code=code, initial_metadata=grpc.aio.Metadata(), trailing_metadata=grpc.aio.Metadata(), details="denied"
    )


@pytest.mark.parametrize(
    "outcome, keeps_renewing",
    [
        pytest.param(rpc_error(grpc.StatusCode.UNAVAILABLE), True, id="controller unreachable"),
        # jmp shell can replace an expired credential (jmp login inside the shell).
        pytest.param(rpc_error(grpc.StatusCode.UNAUTHENTICATED), True, id="credential rejected"),
        pytest.param(jumpstarter_pb2.GetServiceEndpointsResponse(), False, id="telemetry disabled"),
    ],
)
async def test_renew_stops_only_when_renewal_cannot_succeed(outcome, keeps_renewing):
    cred = TelemetryCredential(endpoint("first", 3600))
    discover = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    delays: list[float] = []

    with patch("jumpstarter.common.telemetry_token.sleep", stop_on_second_sleep(delays)):
        if keeps_renewing:
            with pytest.raises(_Stop):
                await cred.renew(discover)
        else:
            await cred.renew(discover)

    assert discover.await_count == 1
    assert cred() == "first"
