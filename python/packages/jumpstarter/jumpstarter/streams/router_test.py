from unittest.mock import AsyncMock, MagicMock

import grpc.aio
import pytest
from anyio import BrokenResourceError

from jumpstarter.streams.router import InvalidStateError, RouterStream


def _finished_call():
    call = MagicMock(spec=grpc.aio.StreamStreamCall)
    error = InvalidStateError("RPC already finished")
    call.write = AsyncMock(side_effect=error)
    call.read = AsyncMock(side_effect=error)
    call.done_writing = AsyncMock(side_effect=error)
    return call


@pytest.mark.anyio
async def test_send_on_a_finished_call_is_a_broken_resource():
    stream = RouterStream(context=_finished_call())

    with pytest.raises(BrokenResourceError):
        await stream.send(b"payload")


@pytest.mark.anyio
async def test_receive_on_a_finished_call_is_a_broken_resource():
    stream = RouterStream(context=_finished_call())

    with pytest.raises(BrokenResourceError):
        await stream.receive()


@pytest.mark.anyio
async def test_closing_a_finished_call_is_quiet():
    stream = RouterStream(context=_finished_call())

    await stream.send_eof()
    await stream.aclose()
