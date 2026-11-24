"""The direct path: a device's HCI to another device's controller simulator. The device's connector and the
simulator are stood in for by sockets; the forward is a relay that can be cut."""

import socket

import anyio
import pytest

from .direct import PROTOCOL, NotH4, RemoteController, RootcanalController, read_packet
from .driver import port_reports

RESET = bytes.fromhex("01030c00")  # HCI Reset
RESET_DONE = bytes.fromhex("040e0401030c00")  # Command Complete (Reset)
ACL = bytes.fromhex("02012005000100040041")  # handle 0x2001, 5 bytes


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Chunks:
    """A stream that gives what it was told to give, a piece at a time."""

    def __init__(self, *pieces: bytes) -> None:
        self.pieces = list(pieces)

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if not self.pieces:
            raise anyio.EndOfStream
        return self.pieces.pop(0)


@pytest.mark.anyio
async def test_packets_are_cut_where_their_headers_say():
    buffer = bytearray()
    stream = Chunks(RESET[:2], RESET[2:] + ACL[:3], ACL[3:] + RESET_DONE)
    assert [await read_packet(stream, buffer) for _ in range(3)] == [RESET, ACL, RESET_DONE]  # ty: ignore[invalid-argument-type]
    with pytest.raises(anyio.EndOfStream):
        await read_packet(stream, buffer)  # ty: ignore[invalid-argument-type]


@pytest.mark.anyio
async def test_what_is_not_h4_is_said_to_be_so():
    with pytest.raises(NotH4, match="0x47"):
        await read_packet(Chunks(b"GET / HTTP/1.1"), bytearray())  # ty: ignore[invalid-argument-type]


def test_the_two_ends_declare_ports_that_a_forward_joins():
    (provides,) = RootcanalController().ports
    assert (provides.name, provides.direction, provides.protocol) == ("bluetooth", "provides", PROTOCOL)
    assert RootcanalController(port=7302).port_address(provides) == ("127.0.0.1", 7302)
    (requires,) = RemoteController().ports
    assert (requires.name, requires.direction, requires.protocol) == ("bluetooth", "requires", PROTOCOL)
    assert requires.listen == "127.0.0.1:7311"
    # as they are reported: without the local addresses
    assert port_reports(RemoteController()) == [
        {
            "name": "bluetooth",
            "direction": "requires",
            "protocol": PROTOCOL,
            "attachment": "socket",
            "transport": "stream",
        }
    ]
    with pytest.raises(ValueError, match="two addresses"):
        RemoteController(guest="127.0.0.1:7301", listen="127.0.0.1:7301")


@pytest.mark.anyio
async def test_a_controller_simulator_counts_its_hosts():
    port = free_port()
    radio = RootcanalController(port=port)
    with pytest.raises(OSError):
        async with radio.connect_port(radio.ports[0]):
            pass
    status = await radio.status()
    assert not status["attached"] and status["last_error"]

    async def simulator(stream):
        async with stream:
            await stream.send(await stream.receive())

    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=port)
    async with listener, anyio.create_task_group() as tasks:
        tasks.start_soon(listener.serve, simulator)
        async with radio.connect_port(radio.ports[0]) as stream:
            await stream.send(RESET)
            assert await stream.receive() == RESET
            status = await radio.status()
            assert status["attached"] and status["peer"] and status["hosts"] == 1
        assert not (await radio.status())["peer"] and (await radio.status())["served"] == 1
        tasks.cancel_scope.cancel()


class Forward:
    """What the exporter and the far end are to the driver: a listener whose connections reach a controller
    that answers a Reset, until they are cut."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.reachable = True
        self.heard: list[bytes] = []
        self.streams: list = []
        self.connections = 0

    async def serve(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        async with await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=self.port) as listener:
            task_status.started()
            await listener.serve(self._connected)

    async def _connected(self, stream) -> None:
        async with stream:
            if not self.reachable:
                return  # (taken, then dropped: the far end is not there)
            self.connections += 1
            self.streams.append(stream)
            buffer = bytearray()
            try:
                while True:
                    packet = await read_packet(stream, buffer)
                    self.heard.append(packet)
                    if packet == RESET:
                        await stream.send(RESET_DONE[:3])  # (in two pieces: the driver passes whole packets)
                        await anyio.sleep(0.01)
                        await stream.send(RESET_DONE[3:])
            except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass

    async def cut(self) -> None:
        for stream in self.streams:
            await stream.aclose()
        self.streams.clear()


async def until(condition, timeout: float = 5.0) -> None:
    with anyio.fail_after(timeout):
        while not condition():
            await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_a_device_keeps_its_connector_while_the_path_comes_and_goes():
    forward = Forward(free_port())
    restarts: list[bool] = []
    radio = RemoteController(
        guest=f"127.0.0.1:{free_port()}", listen=f"127.0.0.1:{forward.port}", retry=0.02, settle=0.05, adb="device"
    )

    async def restart(*, was_set_up: bool) -> None:
        restarts.append(was_set_up)

    radio._restart_bluetooth = restart  # ty: ignore[invalid-assignment]
    guest_host, guest_port = radio.guest.split(":")
    async with anyio.create_task_group() as tasks:
        await tasks.start(forward.serve)
        async with radio.serving():
            # The path is open before the device is powered on: nothing to restart.
            await until(lambda: radio._joined)
            status = await radio.status()
            assert status["running"] and status["listening"] and status["peer"] and not status["attached"]
            assert restarts == []

            async with await anyio.connect_tcp(guest_host, int(guest_port)) as connector:
                await until(lambda: radio._device is not None)
                await connector.send(RESET[:1])
                await connector.send(RESET[1:])
                assert await connector.receive() == RESET_DONE  # whole, though it was sent in two pieces
                assert forward.heard == [RESET]

                # The path is cut: the connector is kept, and what the device says is said to nobody.
                forward.reachable = False
                await forward.cut()
                await until(lambda: not radio._joined)
                await connector.send(ACL)
                await until(lambda: radio._dropped == 1)
                status = await radio.status()
                assert status["attached"] and not status["peer"] and status["carried"] == 1
                assert forward.heard == [RESET]

                # It comes back as a new controller: the stack that had set the old one up starts again.
                forward.reachable = True
                await until(lambda: radio._joined and restarts == [True])
                await connector.send(RESET)
                assert await connector.receive() == RESET_DONE
                status = await radio.status()
                assert (status["paths"], status["restarts"], status["heard"], status["sent"]) == (2, 1, 3, 2)

                # `stop` leaves the controller, `start` looks for it again.
                await radio.stop()
                await until(lambda: not radio._joined)
                assert not (await radio.status())["running"]
                await radio.start()
                await radio.start()
                await until(lambda: radio._joined and restarts == [True, True])
            await until(lambda: radio._device is None)
        tasks.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_device_that_only_ever_talked_to_nobody_is_switched_on_not_off_and_on():
    forward = Forward(free_port())
    forward.reachable = False
    commands: list[tuple[str, ...]] = []
    radio = RemoteController(
        guest=f"127.0.0.1:{free_port()}", listen=f"127.0.0.1:{forward.port}", retry=0.02, settle=0.05, adb="device"
    )

    async def adb(*arguments: str) -> str:
        commands.append(arguments)
        return ""

    radio._adb = adb  # ty: ignore[invalid-assignment]
    guest_host, guest_port = radio.guest.split(":")
    async with anyio.create_task_group() as tasks:
        await tasks.start(forward.serve)
        async with radio.serving(), await anyio.connect_tcp(guest_host, int(guest_port)) as connector:
            await connector.send(RESET)  # (the far device is not powered on yet)
            await until(lambda: radio._dropped == 1)
            assert not radio._joined and commands == []
            forward.reachable = True
            await until(lambda: radio._joined and len(commands) == 1)
            assert commands == [("shell", "cmd bluetooth_manager enable")]
        tasks.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_stack_that_was_set_up_is_switched_off_then_on():
    commands: list[tuple[str, ...]] = []
    radio = RemoteController(adb="127.0.0.1:6520")

    async def adb(*arguments: str) -> str:
        commands.append(arguments)
        return ""

    radio._adb = adb  # ty: ignore[invalid-assignment]
    await radio._restart_bluetooth(was_set_up=True)
    ((shell, script),) = commands
    assert shell == "shell"
    assert [step.split()[-1] for step in script.split(";")] == ["disable", "wait-for-state:STATE_OFF", "enable"]
