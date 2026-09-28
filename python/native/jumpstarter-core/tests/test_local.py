"""Exercise the native ABI and Windows security contract with real sockets."""

import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("Windows native transport", allow_module_level=True)

import ntsecuritycon
import win32api
import win32security
from jumpstarter_core.local import PrivateDirectory, UnixListener, UnixStream


def wait_for(operation, *, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = operation()
        if result is not None and result is not False:
            return result
        time.sleep(0.001)
    pytest.fail("native nonblocking operation did not complete")


def assert_transfer(sender, receiver, payload):
    sent = 0
    received = bytearray()
    deadline = time.monotonic() + 3
    while sent < len(payload) or len(received) < len(payload):
        assert time.monotonic() < deadline
        if sent < len(payload):
            count = sender.try_send(payload[sent:])
            if count is not None:
                assert count > 0
                sent += count
        data = receiver.try_recv(4096)
        if data is not None:
            assert data
            received.extend(data)
    assert received == payload


@contextmanager
def connection():
    directory = PrivateDirectory.create()
    listener = UnixListener.bind(directory.socket_path)
    client = server = None
    try:
        assert listener.try_accept() is None
        client = UnixStream.connect(directory.socket_path)
        wait_for(client.finish_connect)
        server = wait_for(listener.try_accept)
        yield directory, listener, client, server
    finally:
        for socket in (client, server, listener):
            if socket is not None:
                socket.close()
        directory.close()


def test_directory_dacl_is_private_and_socket_inherits_it():
    with connection() as (directory, _, _, _):
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        expected = {win32security.ConvertSidToStringSid(user), "S-1-5-18"}
        for path in (Path(directory.socket_path).parent, Path(directory.socket_path)):
            descriptor = win32security.GetNamedSecurityInfo(
                str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
            )
            if path.is_dir():
                assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            acl = descriptor.GetSecurityDescriptorDacl()
            assert acl is not None
            assert acl.GetAceCount() == 2
            actual = set()
            for index in range(acl.GetAceCount()):
                (ace_type, _), access, sid = acl.GetAce(index)
                assert ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
                assert access == ntsecuritycon.FILE_ALL_ACCESS
                actual.add(win32security.ConvertSidToStringSid(sid))
            assert actual == expected


def test_binary_io_partial_writes_and_half_close():
    with connection() as (_, _, client, server):
        assert server.try_recv(4096) is None
        payload = bytes(range(256)) * 1024
        assert_transfer(client, server, payload)
        client.shutdown_write()
        assert wait_for(lambda: server.try_recv(4096)) == b""
        assert_transfer(server, client, b"after-eof")


def test_close_is_idempotent_and_listener_close_preserves_streams():
    with connection() as (_, listener, client, server):
        listener.close()
        listener.close()
        with pytest.raises(OSError) as error:
            listener.try_accept()
        assert error.value.winerror == 10038  # WSAENOTSOCK
        assert_transfer(client, server, b"alive")
        client.close()
        client.close()
        with pytest.raises(OSError):
            client.try_recv(4096)
        with pytest.raises(OSError):
            client.try_send(b"closed")


def test_bind_never_replaces_existing_path():
    directory = PrivateDirectory.create()
    path = Path(directory.socket_path)
    try:
        path.write_bytes(b"existing")
        with pytest.raises(OSError):
            UnixListener.bind(str(path))
        assert path.read_bytes() == b"existing"
    finally:
        directory.close()


def test_directory_cleanup_does_not_remove_unrelated_children():
    directory = PrivateDirectory.create()
    child = Path(directory.socket_path).parent / "keep"
    try:
        child.write_bytes(b"keep")
        with pytest.raises(OSError):
            directory.close()
        assert child.read_bytes() == b"keep"
    finally:
        child.unlink(missing_ok=True)
        directory.close()
        directory.close()
    assert not child.parent.exists()


def test_invalid_paths_fail_before_creating_any_socket(tmp_path):
    with pytest.raises(OSError, match="absolute"):
        UnixListener.bind("relative")
    with pytest.raises(OSError, match="NUL"):
        UnixStream.connect(str(tmp_path / "bad") + "\0suffix")
    with pytest.raises(OSError, match="107"):
        UnixListener.bind(str(tmp_path / ("x" * 200)))
    assert list(tmp_path.iterdir()) == []

