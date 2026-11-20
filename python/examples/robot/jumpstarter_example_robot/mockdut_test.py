import socket
import time

from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from .mockdut import MockDut, MockDutConsole, MockDutPower
from jumpstarter.common.utils import serve


def _read_until(sock: socket.socket, marker: bytes, timeout: float = 5.0) -> bytes:
    data = b""
    deadline = time.monotonic() + timeout
    sock.settimeout(0.2)
    while marker not in data and time.monotonic() < deadline:
        try:
            data += sock.recv(4096)
        except TimeoutError:
            continue
    assert marker in data, data
    return data


def test_boot_login_and_commands():
    dut = MockDut(children={"power": MockDutPower(board="t1"), "console": MockDutConsole(board="t1")})
    with serve(dut) as client:
        assert client.raw_status() == "off"
        with TcpPortforwardAdapter(client=client.console) as (host, port), socket.create_connection((host, port)) as s:
            client.power.on()
            assert b"Linux version 6.6.0-mock" in _read_until(s, b"mockdut login: ")
            s.sendall(b"root\r")
            _read_until(s, b"Password: ")
            s.sendall(b"secret\r")
            _read_until(s, b"root@mockdut:~# ")
            s.sendall(b"uname -r\r")
            assert b"6.6.0-mock" in _read_until(s, b"root@mockdut:~# ")
        client.should_be_powered()
        client.reboot(wait=0)
        assert client.power.status() == "on"
        readings = []
        for reading in client.power.read():
            readings.append(reading)
            if len(readings) == 2:
                break
        assert readings[0].voltage == 5.0
