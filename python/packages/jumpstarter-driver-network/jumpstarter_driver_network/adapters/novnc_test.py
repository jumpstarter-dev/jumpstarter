from contextlib import closing
from urllib.parse import parse_qsl, urlparse

from anyio.from_thread import start_blocking_portal
from websocket import create_connection

from ..driver import TcpNetwork
from .novnc import NovncAdapter
from jumpstarter.common import TemporaryTcpListener
from jumpstarter.common.utils import serve


async def echo_handler(stream):
    async with stream:
        while True:
            try:
                await stream.send(await stream.receive())
            except Exception:
                pass


def test_client_adapter_novnc():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryTcpListener(echo_handler, local_host="127.0.0.1")) as addr:
            with serve(TcpNetwork(host=addr[0], port=addr[1])) as client:
                with NovncAdapter(client=client) as url:
                    parsed = dict(parse_qsl(urlparse(url).query))
                    with closing(create_connection(f"ws://{parsed['host']}:{parsed['port']}")) as ws:
                        ws.ping()
                        ws.send_bytes(b"hello")
                        assert ws.recv() == b"hello"
