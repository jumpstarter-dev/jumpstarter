"""The gateway here is a stand-in for an edge gateway's attach port: it takes the connections the driver
keeps waiting, and a test plays the device on the Wi-Fi by speaking on one of them."""

from contextlib import asynccontextmanager

import anyio
import pytest
from anyio import create_memory_object_stream

from .gateway import GatewayWifi
from jumpstarter.common import TemporaryTcpListener
from jumpstarter.driver import Port
from jumpstarter.exporter import Session

pytestmark = pytest.mark.anyio


def free_address() -> str:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe.getsockname()[1]}"


@asynccontextmanager
async def accepting():
    """A listener that hands each connection it takes to the test."""
    taken, connections = create_memory_object_stream[object](16)

    async def handler(stream):
        done = anyio.Event()
        await taken.send((stream, done))
        await done.wait()

    async with TemporaryTcpListener(handler) as address:
        yield f"{address[0]}:{address[1]}", connections


@asynccontextmanager
async def serving(driver):
    with Session(root_device=driver) as session:
        async with session.serve_unix_async():
            yield session


async def status_when(driver, what, timeout: float = 5):
    with anyio.fail_after(timeout):
        while True:
            status = await driver.status()
            if what(status):
                return status
            await anyio.sleep(0.02)


def test_its_port_is_requires_because_connections_start_on_the_wifi():
    wifi = GatewayWifi()
    (port,) = wifi.ports
    assert (port.name, port.direction, port.listen) == ("wifi-service", "requires", "127.0.0.1:7411")
    assert [report.name for report in wifi.report().ports] == ["wifi-service"]
    with pytest.raises(ValueError, match="requires"):
        GatewayWifi(ports=[Port(name="wifi-service", direction="provides", connect="127.0.0.1:1")])


async def test_a_devices_connection_is_handed_to_the_port_and_another_waits_in_its_place():
    async with accepting() as (attach, waiting), accepting() as (listen, forwarded):
        driver = GatewayWifi(attach=attach, listen=listen, waiting=2, retry=0.05)
        async with serving(driver):
            assert (await driver.status())["running"] is False
            await driver.start()
            await driver.start()  # (twice does nothing)
            status = await status_when(driver, lambda s: s["waiting"] == 2)
            assert status["attached"] and not status["peer"]

            with anyio.fail_after(5):
                device, device_done = await waiting.receive()
                await waiting.receive()
                # Nothing reaches the port while no device has spoken.
                await anyio.sleep(0.1)
                assert forwarded.statistics().current_buffer_used == 0

                # A device on the Wi-Fi connects: the gateway passes on what it says.
                await device.send(b"GET / HTTP/1.1\r\n\r\n")
                service, service_done = await forwarded.receive()
                assert await service.receive() == b"GET / HTTP/1.1\r\n\r\n"
                await service.send(b"HTTP/1.1 200 OK\r\n\r\n")
                assert await device.receive() == b"HTTP/1.1 200 OK\r\n\r\n"

                # ... and a new connection waits at the gateway for the next device.
                await waiting.receive()
            status = await status_when(driver, lambda s: s["waiting"] == 2 and s["peer"])
            assert status["joined"] == 1 and status["active"] == 1

            # The service has said all it has to say: the device is told so, and hangs up in its turn.
            await service.aclose()
            service_done.set()
            with anyio.fail_after(5), pytest.raises((anyio.EndOfStream, anyio.BrokenResourceError)):
                await device.receive()
            await device.aclose()
            device_done.set()
            await status_when(driver, lambda s: not s["peer"])

            await driver.stop()
            assert await driver.status() == {
                "running": False, "attached": False, "peer": False,
                "waiting": 0, "active": 0, "joined": 0, "refused": 0, "error": "",
            }  # fmt: skip


async def test_without_a_forward_a_devices_connection_is_closed():
    async with accepting() as (attach, waiting):
        driver = GatewayWifi(attach=attach, listen=free_address(), waiting=1, retry=0.05)
        async with serving(driver):
            await driver.start()
            with anyio.fail_after(5):
                device, done = await waiting.receive()
                await device.send(b"hello?")
                with pytest.raises((anyio.EndOfStream, anyio.BrokenResourceError)):
                    await device.receive()
                done.set()
            status = await status_when(driver, lambda s: s["refused"] == 1)
            assert status["joined"] == 0


async def test_a_gateway_that_is_not_there_is_looked_for_until_it_is():
    attach = free_address()
    driver = GatewayWifi(attach=attach, listen=free_address(), waiting=1, retry=0.05)
    async with serving(driver):
        await driver.start()
        status = await status_when(driver, lambda s: s["error"])
        assert status["running"] and not status["attached"]
        assert "cannot reach the gateway" in status["error"]

        async def handler(stream):
            await anyio.sleep_forever()

        host, port = attach.split(":")
        async with TemporaryTcpListener(handler, local_host=host, local_port=int(port)):
            status = await status_when(driver, lambda s: s["attached"])
            assert status["error"] == ""
