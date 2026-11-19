from types import SimpleNamespace

import grpc
import pytest
from anyio import fail_after, move_on_after

from .rpcs_in_flight import RpcsInFlight


async def echo(request, context):
    return request


@pytest.mark.anyio
async def test_finished_waits_for_the_rpc_to_end_not_only_its_handler():
    rpcs = RpcsInFlight()
    done_callbacks = []
    context = SimpleNamespace(add_done_callback=done_callbacks.append)

    async def continuation(handler_call_details):
        return grpc.unary_unary_rpc_method_handler(echo)

    handler = await rpcs.intercept_service(continuation, None)
    # The handler returned, but gRPC still has the response to send in the RPC's task.
    assert await handler.unary_unary(b"ping", context) == b"ping"
    with move_on_after(0.1) as still_sending:
        await rpcs.finished()
    assert still_sending.cancelled_caught

    for callback in done_callbacks:
        callback(context)
    with fail_after(1):
        await rpcs.finished()
