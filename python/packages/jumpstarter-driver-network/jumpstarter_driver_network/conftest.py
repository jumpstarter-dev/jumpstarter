import contextlib

import pytest
from anyio import EndOfStream
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import TemporaryTcpListener


async def echo_handler(stream):
    async with stream:
        with contextlib.suppress(EndOfStream):
            while True:
                await stream.send(await stream.receive())


@pytest.fixture
def tcp_echo_server():
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(TemporaryTcpListener(echo_handler, local_host="127.0.0.1")) as addr,
    ):
        yield addr
