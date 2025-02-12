import pytest
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import TemporaryTcpListener


async def echo_handler(stream):
    async with stream:
        while True:
            try:
                await stream.send(await stream.receive())
            except Exception:
                pass


@pytest.fixture
def tcp_echo_server():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryTcpListener(echo_handler, local_host="127.0.0.1")) as addr:
            yield addr
