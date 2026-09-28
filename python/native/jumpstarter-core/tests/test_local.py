"""Exercise the native ABI and Windows security contract with real sockets."""

import subprocess
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


def dacl_entries(path):
    descriptor = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION,
    )
    acl = descriptor.GetSecurityDescriptorDacl()
    assert acl is not None
    entries = set()
    for index in range(acl.GetAceCount()):
        (ace_type, flags), access, sid = acl.GetAce(index)
        assert ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
        assert access == ntsecuritycon.FILE_ALL_ACCESS
        entries.add((win32security.ConvertSidToStringSid(sid), flags & ~win32security.INHERITED_ACE))
    assert len(entries) == acl.GetAceCount()
    return descriptor, entries


def test_directory_dacl_is_private_and_children_inherit_concrete_owner_sids():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        # New objects are owned by the token's default owner: this user, or
        # Administrators for an elevated administrator on some Windows editions.
        owner = win32security.GetTokenInformation(token, win32security.TokenOwner)
    finally:
        token.Close()
    inherit = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
    with connection() as (directory, _, _, _):
        socket = Path(directory.socket_path)
        key = socket.parent / "identity"
        key.write_bytes(b"private")
        try:
            descriptor, entries = dacl_entries(socket.parent)
            assert descriptor.GetSecurityDescriptorOwner() == owner
            assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            # OWNER RIGHTS covers the directory; CREATOR OWNER applies only to children.
            assert entries == {
                ("S-1-3-4", 0),
                ("S-1-3-0", inherit | win32security.INHERIT_ONLY_ACE),
                ("S-1-5-18", inherit),
            }
            for child in (socket, key):
                descriptor, entries = dacl_entries(child)
                assert descriptor.GetSecurityDescriptorOwner() == owner
                # Concrete SIDs only: OpenSSH rejects keys whose ACL names OWNER RIGHTS.
                assert entries == {(win32security.ConvertSidToStringSid(owner), 0), ("S-1-5-18", 0)}
        finally:
            key.unlink()


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


def test_console_mode_rejects_redirected_stdout_without_closing_it():
    # Exercise the installed wheel in a child whose stdout is a real pipe.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "from jumpstarter_core.console import OutputMode\n"
                "guard = OutputMode()\n"
                "try:\n"
                "    with guard:\n"
                "        raise AssertionError('redirected stdout accepted as a console')\n"
                "except OSError:\n"
                "    pass\n"
                "guard.close()\n"
                "print('original stdout remains open')\n"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "original stdout remains open\n"
