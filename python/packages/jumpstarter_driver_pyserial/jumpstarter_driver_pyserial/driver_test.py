from typing import cast

from jumpstarter.common.utils import serve

from .client import PySerialClient
from .driver import PySerial


def test_bare_pyserial():
    with serve(PySerial(url="loop://")) as client:
        with client.stream() as stream:
            stream.send(b"hello")
            assert "hello".startswith(stream.receive().decode("utf-8"))

def test_bare_open_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        stream = client.open_stream()
        stream.send(b"hello")
        assert "hello".startswith(stream.receive().decode("utf-8"))
        client.close()

def test_pexpect_open_pyserial_forget_close():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client) # this is only necessary for the editor to recognize the client methods
        pexpect = client.open()
        pexpect.sendline("hello")
        assert pexpect.expect("hello") == 0

def test_pexpect_open_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        pexpect = client.open()
        pexpect.sendline("hello")
        assert pexpect.expect("hello") == 0
        client.close()

def test_pexpect_context_pyserial():
    with serve(PySerial(url="loop://")) as client:
        client = cast(PySerialClient, client)
        with client.pexpect() as pexpect:
            pexpect.sendline("hello")
            assert pexpect.expect("hello") == 0
