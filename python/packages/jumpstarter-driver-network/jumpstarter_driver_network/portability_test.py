"""Network client behavior through a TCP SDK session, without Unix sockets."""

import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from io import BytesIO, StringIO

import pytest
from anyio.from_thread import start_blocking_portal
from pexpect import EOF, TIMEOUT

from .adapters import PexpectAdapter, TcpPortforwardAdapter
from .adapters.pexpect import _SocketSpawn
from .driver import EchoNetwork, TcpNetwork
from jumpstarter.client import client_from_path
from jumpstarter.common import ExporterStatus, TemporaryTcpListener
from jumpstarter.exporter import Session


@contextmanager
def tcp_client(driver):
    with start_blocking_portal() as portal, ExitStack() as stack, Session(root_device=driver) as session:
        session.update_status(ExporterStatus.LEASE_READY)
        with (
            portal.wrap_async_context_manager(session.serve_tcp_async("127.0.0.1", 0)) as port,
            portal.wrap_async_context_manager(
                client_from_path(f"127.0.0.1:{port}", portal, stack, allow=[], unsafe=True, insecure=True)
            ) as client,
        ):
            yield client


def exchange(address, payload):
    with socket.create_connection(address, timeout=5) as stream:
        stream.sendall(payload)
        received = bytearray()
        while len(received) < len(payload):
            chunk = stream.recv(len(payload) - len(received))
            assert chunk, "forwarded stream closed before delivering the payload"
            received.extend(chunk)
        assert received == payload


def assert_listener_closed(address):
    with pytest.raises(OSError):
        socket.create_connection(address, timeout=1)


def test_tcp_forwarding_concurrent_binary_streams_and_cleanup(tcp_echo_server):
    with tcp_client(TcpNetwork(host=tcp_echo_server[0], port=tcp_echo_server[1])) as client:
        with TcpPortforwardAdapter(client=client) as address, ThreadPoolExecutor(max_workers=3) as workers:
            futures = [workers.submit(exchange, address, bytes(range(256)) * (n + 1)) for n in range(3)]
            for future in futures:
                future.result(timeout=10)
        assert_listener_closed(address)


def test_pexpect_socket_binary_matching_timeout_and_cleanup():
    with tcp_client(EchoNetwork()) as client:
        with PexpectAdapter(client=client) as expect:
            payload = b"\x00\xffready\r\n"
            expect.logfile_read = BytesIO()
            expect.send(payload)
            assert expect.expect_exact(payload, timeout=5) == 0
            assert expect.logfile_read.getvalue() == payload
            with pytest.raises(TIMEOUT):
                expect.expect_exact(b"missing", timeout=0.05)
        assert not expect.isalive()


def test_pexpect_socket_decodes_fragmented_text_and_logs(tcp_echo_server):
    payload = "ready: \u00e9\U0001f680\r\n"
    with socket.create_connection(tcp_echo_server, timeout=5) as stream:
        expect = _SocketSpawn(stream, encoding="utf-8", maxread=1)
        expect.logfile_read = StringIO()
        expect.send(payload)
        assert expect.expect_exact(payload, timeout=5) == 0
        assert expect.logfile_read.getvalue() == payload


def test_pexpect_socket_eof():
    async def finish(stream):
        async with stream:
            await stream.send(b"finished")

    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(TemporaryTcpListener(finish)) as upstream,
        tcp_client(TcpNetwork(host=upstream[0], port=upstream[1])) as client,
        PexpectAdapter(client=client) as expect,
    ):
        assert expect.expect_exact(b"finished", timeout=5) == 0
        assert expect.expect(EOF, timeout=5) == 0
