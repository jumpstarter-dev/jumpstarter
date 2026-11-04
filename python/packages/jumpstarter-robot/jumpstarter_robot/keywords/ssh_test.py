"""Tests for the SSH keywords, against an in-process paramiko SSH server.

The server is exported the way a lab exports a Linux board: an ``SSHWrapper``
driver with a ``TcpNetwork`` child pointing at the server's address and the
client key as ``ssh_identity``. The suites then use SSHLibrary over the
Jumpstarter tunnel, as a real test would.
"""

from __future__ import annotations

import io
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import paramiko
import pytest
import robot
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_ssh.driver import SSHWrapper
from robot.api import ExecutionResult, Failure
from robot.result import Result

from .. import context
from ..testing import use_client
from . import ssh
from .ssh import SshEndpoint, SshKeywords, is_mitm, split_options, write_identity
from jumpstarter.common.utils import serve

USERNAME = "tester"
PASSWORD = "s3cret"
SSH_COMMAND = (
    "ssh -F /dev/null -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "
    "-o BatchMode=yes -o IdentitiesOnly=yes"
)


# ---- an in-process SSH server ----


class _Interface(paramiko.ServerInterface):
    def __init__(self, server: SshServer):
        self.server = server
        self.commands: dict[int, str | None] = {}
        self.ready: dict[int, threading.Event] = {}

    def check_channel_request(self, kind, chanid):
        if kind != "session":
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        self.ready[chanid] = threading.Event()
        return paramiko.OPEN_SUCCEEDED

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_auth_publickey(self, username, key):
        if username == USERNAME and key.get_base64() == self.server.client_key.get_base64():
            self.server.logins.append("publickey")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        if username == USERNAME and password == PASSWORD:
            self.server.logins.append("password")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_exec_request(self, channel, command):
        self.commands[channel.get_id()] = command.decode() if isinstance(command, bytes) else command
        self.ready[channel.get_id()].set()
        return True

    def check_channel_shell_request(self, channel):
        # SSHLibrary's login reads the login banner from an interactive shell
        self.commands[channel.get_id()] = None
        self.ready[channel.get_id()].set()
        return True

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True


@dataclass
class SshServer:
    """A paramiko SSH server on loopback that runs exec requests with /bin/sh."""

    host_key: paramiko.RSAKey
    client_key: paramiko.RSAKey
    sock: socket.socket = field(default_factory=lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    logins: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    transports: list[paramiko.Transport] = field(default_factory=list)
    running: bool = True

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def start(self) -> SshServer:
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self.running = False
        for transport in self.transports:
            transport.close()
        self.sock.close()

    def _accept(self) -> None:
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        interface = _Interface(self)
        try:
            transport.start_server(server=interface)
        except (paramiko.SSHException, EOFError, OSError):
            return
        while transport.is_active() and self.running:
            channel = transport.accept(0.2)
            if channel is not None:
                threading.Thread(target=self._run, args=(channel, interface), daemon=True).start()

    def _run(self, channel: paramiko.Channel, interface: _Interface) -> None:
        if not interface.ready[channel.get_id()].wait(10):
            channel.close()
            return
        command = interface.commands[channel.get_id()]
        time.sleep(0.05)  # let the request's reply go out before the output
        if command is None:
            self._shell(channel)
            return
        self.commands.append(command)
        result = self._execute(command)
        channel.sendall(result.stdout)
        channel.sendall_stderr(result.stderr)
        channel.send_exit_status(result.returncode)
        channel.close()

    @staticmethod
    def _execute(command: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/sh", "-c", command], capture_output=True, timeout=30, check=False)

    def _shell(self, channel: paramiko.Channel) -> None:
        """A line-at-a-time shell with a ``$ `` prompt, enough for login and Write/Read Until."""
        channel.sendall(b"welcome\n$ ")
        buffer = b""
        while True:
            try:
                data = channel.recv(1024)
            except (OSError, EOFError):
                return
            if not data:
                channel.close()
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                result = self._execute(line.decode().strip())
                # echo the input, as a terminal does
                channel.sendall(line + b"\n" + result.stdout + result.stderr + b"$ ")


def private_pem(key: paramiko.RSAKey) -> str:
    out = io.StringIO()
    key.write_private_key(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def keys() -> tuple[paramiko.RSAKey, paramiko.RSAKey]:
    return paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)


@pytest.fixture
def server(keys) -> Iterator[SshServer]:
    running = SshServer(host_key=keys[0], client_key=keys[1]).start()
    try:
        yield running
    finally:
        running.stop()


def exporter(server: SshServer) -> Composite:
    """``ssh`` logs in with the exporter's key, ``ssh_pw`` has none and needs a password."""
    return Composite(
        children={
            "ssh": SSHWrapper(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=server.port)},
                default_username=USERNAME,
                ssh_identity=private_pem(server.client_key),
                ssh_command=SSH_COMMAND,
            ),
            "ssh_pw": SSHWrapper(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=server.port)},
                default_username=USERNAME,
                ssh_command=SSH_COMMAND,
            ),
            "nouser": SSHWrapper(children={"tcp": TcpNetwork(host="127.0.0.1", port=server.port)}),
        }
    )


@pytest.fixture
def served(server) -> Iterator[Any]:
    with serve(exporter(server)) as client, use_client(client):
        try:
            yield client
        finally:
            context.end_run()
            ssh._opened.clear()


def run_suite(tmp_path: Path, text: str) -> Result:
    suite = tmp_path / "suite.robot"
    suite.write_text(text)
    rc = robot.run(
        str(suite),
        outputdir=str(tmp_path),
        output=str(tmp_path / "output.xml"),
        report="NONE",
        log="NONE",
        console="none",
    )
    result = ExecutionResult(str(tmp_path / "output.xml"))
    statuses = {test.name: (test.status, test.message) for test in result.suite.all_tests}
    assert rc == 0, statuses
    return result


def statuses(result: Result) -> dict[str, str]:
    return {test.name: test.status for test in result.suite.all_tests}


# ---- Robot suites over the real tunnel ----


SUITE = """
*** Settings ***
Library    OperatingSystem
Library    SSHLibrary    timeout=10s
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    ssh    client=jumpstarter_driver_ssh.client.SSHWrapperClient    prefix=Dut
...    AS    dut

*** Test Cases ***
Key Login And Execute Command
    ${index}=    Open SSH Connection    ssh    alias=dut
    Should Be Equal As Integers    ${index}    1
    ${out}=    Execute Command    echo hello-from-device
    Should Be Equal    ${out}    hello-from-device
    ${err}    ${rc}=    Execute Command    echo oops >&2; exit 3
    ...    return_stdout=False    return_stderr=True    return_rc=True
    Should Be Equal    ${err}    oops
    Should Be Equal As Integers    ${rc}    3
    Close SSH Connection    dut
    Run Keyword And Expect Error    *    Execute Command    echo closed

Password Fallback
    Open SSH Connection    ssh_pw    password=${PASSWORD}
    ${out}=    Execute Command    whoami >/dev/null; echo password-ok
    Should Be Equal    ${out}    password-ok
    Close SSH Connection

Wrong Password Fails
    Run Keyword And Expect Error    *    Open SSH Connection    ssh_pw    password=wrong

No Username Fails
    Run Keyword And Expect Error    *has no default_username*    Open SSH Connection    nouser

Explicit Username
    Run Keyword And Expect Error    *    Open SSH Connection    ssh    username=intruder

Two Connections And Switching
    Open SSH Connection    ssh    alias=one
    Open SSH Connection    ssh_pw    alias=two    password=${PASSWORD}
    Switch Connection    one
    ${out}=    Execute Command    echo first
    Should Be Equal    ${out}    first
    Close SSH Connection    two
    ${out}=    Execute Command    echo still-one
    Should Be Equal    ${out}    still-one
    Close SSH Connection    one

Scoped To The Test
    Open SSH Connection    ssh    alias=scoped    scope=test    prompt=$
    ${out}=    Execute Command    echo scoped
    Should Be Equal    ${out}    scoped
    Write    echo interactive
    ${out}=    Read Until Prompt
    Should Contain    ${out}    interactive

Scoped Connection Was Closed With Its Test
    Run Keyword And Expect Error    *    Switch Connection    scoped

Unknown Option Fails
    Run Keyword And Expect Error    *unknown option 'bogus'*    Open SSH Connection    ssh    bogus=1

SSHLibrary Options Are Passed On
    Open SSH Connection    ssh    alias=opts    prompt=$    term_type=vt100    look_for_keys=False
    ${conf}=    Get Connection    opts
    Should Be Equal    ${conf.prompt}    $
    Should Be Equal    ${conf.term_type}    vt100
    Close SSH Connection    opts

Get SSH Endpoint
    ${ep}=    Get SSH Endpoint    ssh
    Should Be Equal    ${ep.host}    127.0.0.1
    Should Be Equal    ${ep.username}    tester
    OperatingSystem.File Should Exist    ${ep.keyfile}
    ${mode}=    Evaluate    oct(os.stat($ep.keyfile).st_mode & 0o777)    modules=os
    Should Be Equal    ${mode}    0o600
    Open Connection    ${ep.host}    port=${ep.port}
    Login With Public Key    ${ep.username}    ${ep.keyfile}
    ${out}=    Execute Command    echo by-endpoint
    Should Be Equal    ${out}    by-endpoint
    Close Connection

Node Shortcut
    ${index}=    Dut Connect    alias=shortcut
    ${out}=    Execute Command    echo shortcut
    Should Be Equal    ${out}    shortcut
    Close SSH Connection    shortcut

Connect To Device Dispatches To SSH
    ${id}=    Connect To Device    ssh    alias=dispatched
    Should Be Equal    ${id}    dispatched
    ${out}=    Execute Command    echo dispatched
    Should Be Equal    ${out}    dispatched
    Close SSH Connection    dispatched
"""


def test_suite_over_the_tunnel(served, server, tmp_path):
    result = run_suite(tmp_path, SUITE.replace("${PASSWORD}", PASSWORD))
    assert set(statuses(result).values()) == {"PASS"}
    assert "publickey" in server.logins and "password" in server.logins
    assert "echo hello-from-device" in server.commands


def test_open_without_sshlibrary_fails_with_a_hint(served, tmp_path):
    result = run_suite(
        tmp_path,
        """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter

*** Test Cases ***
Missing SSHLibrary
    Run Keyword And Expect Error    *Library*SSHLibrary*jumpstarter-robot?ssh?*    Open SSH Connection
""",
    )
    assert statuses(result) == {"Missing SSHLibrary": "PASS"}


def test_wrong_driver_fails(served):
    with pytest.raises(Failure, match="not an SSH driver"):
        SshKeywords().get_ssh_endpoint("ssh.tcp")


def test_missing_driver_fails(served):
    with pytest.raises(Failure, match="not present"):
        SshKeywords().get_ssh_endpoint("nothing")


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the ssh binary")
def test_execute_over_ssh(served, server):
    keywords = SshKeywords()
    result = keywords.execute_over_ssh("echo over-ssh")
    assert result.rc == 0
    assert result.stdout == "over-ssh"
    result = keywords.execute_over_ssh("echo bad >&2; exit 4", expected_rc=None)
    assert (result.rc, result.stderr) == (4, "bad")
    with pytest.raises(Failure, match="exited with 4, expected 0"):
        keywords.execute_over_ssh("exit 4")


# ---- key files and forwards ----


def test_endpoint_writes_a_private_key_and_removes_it(served, server):
    endpoint = SshKeywords().get_ssh_endpoint("ssh")
    keyfile = Path(endpoint.keyfile)
    assert keyfile.read_text() == private_pem(server.client_key)
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(keyfile.parent.stat().st_mode) == 0o700
    with socket.create_connection((endpoint.host, endpoint.port), timeout=5) as conn:
        assert conn.recv(4).startswith(b"SSH-")
    context.end_run()
    assert not keyfile.exists()
    assert not keyfile.parent.exists()
    with pytest.raises(OSError):
        socket.create_connection((endpoint.host, endpoint.port), timeout=2).close()


def test_endpoint_without_identity_has_no_keyfile(served):
    endpoint = SshKeywords().get_ssh_endpoint("ssh_pw")
    assert endpoint.keyfile is None
    assert endpoint.username == USERNAME


def test_session_close_removes_the_key(server):
    with serve(exporter(server)) as client:
        with use_client(client):
            keyfile = Path(SshKeywords().get_ssh_endpoint("ssh").keyfile)
            assert keyfile.exists()
        # use_client closed the session, which closes what was opened on it
        assert not keyfile.exists()
    context.end_run()


def test_write_identity_adds_a_trailing_newline(tmp_path):
    from contextlib import ExitStack

    with ExitStack() as stack:
        keyfile = Path(write_identity("KEY", stack))
        assert keyfile.read_text() == "KEY\n"
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert not keyfile.exists()


# ---- unit level: SSHLibrary calls, ssh-mitm ----


class FakeConfig:
    def __init__(self, index: int):
        self.index = index


class FakeClient:
    def __init__(self, index: int):
        self.config = FakeConfig(index)
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __bool__(self) -> bool:
        return not self.closed


class FakeSSHLibrary:
    """Records SSHLibrary calls; mirrors the real method signatures."""

    def __init__(self, fail_login: bool = False):
        self.calls: list[tuple] = []
        self.connections: list[FakeClient] = []
        self.current: Any = None
        self.fail_login = fail_login

    def open_connection(self, host, alias=None, port=22, timeout=None, newline=None, prompt=None, term_type=None):
        self.calls.append(("open", host, alias, port, {"timeout": timeout, "prompt": prompt}))
        self.current = FakeClient(len(self.connections) + 1)
        self.connections.append(self.current)
        return self.current.config.index

    def login(self, username=None, password=None, allow_agent=False, look_for_keys=False, delay="0.5 seconds"):
        self.calls.append(("login", username, password, {"look_for_keys": look_for_keys, "delay": delay}))
        if self.fail_login:
            raise RuntimeError("Authentication failed")

    def login_with_public_key(self, username=None, keyfile=None, password="", allow_agent=False, delay="0.5 s"):
        self.calls.append(("key", username, keyfile, {"allow_agent": allow_agent}))
        if self.fail_login:
            raise RuntimeError("Authentication failed")

    def switch_connection(self, index_or_alias):
        previous = self.current.config.index if self.current else None
        self.current = self.connections[int(index_or_alias) - 1]
        return previous

    def close_connection(self):
        self.calls.append(("close", self.current.config.index))
        self.current.close()
        self.current = None


@contextmanager
def fake_library(monkeypatch, library: FakeSSHLibrary) -> Generator[FakeSSHLibrary]:
    monkeypatch.setattr(ssh, "sshlibrary", lambda: library)
    yield library


def test_key_login_and_close(served, monkeypatch):
    library = FakeSSHLibrary()
    with fake_library(monkeypatch, library):
        keywords = SshKeywords()
        index = keywords.open_ssh_connection("ssh", alias="dut", timeout="5s")
        assert index == 1
        opened, login = library.calls
        assert opened[0] == "open" and opened[1] == "127.0.0.1" and opened[2] == "dut"
        assert opened[4]["timeout"] == "5s"
        assert login[0] == "key" and login[1] == USERNAME
        keyfile = Path(login[2])
        assert keyfile.exists()
        keywords.close_ssh_connection()
        assert ("close", 1) in library.calls
        assert not keyfile.exists()
        assert not ssh._opened


def test_password_given_with_a_key_logs_in_with_the_password(served, monkeypatch):
    library = FakeSSHLibrary()
    with fake_library(monkeypatch, library):
        SshKeywords().open_ssh_connection("ssh", password="pw", look_for_keys="False", delay="1s")
    login = library.calls[1]
    assert login[:3] == ("login", USERNAME, "pw")
    assert login[3] == {"look_for_keys": False, "delay": "1s"}


def test_failed_login_closes_connection_and_forward(served, monkeypatch):
    library = FakeSSHLibrary(fail_login=True)
    with fake_library(monkeypatch, library), pytest.raises(RuntimeError, match="Authentication failed"):
        SshKeywords().open_ssh_connection("ssh")
    keyfile = Path(library.calls[1][2])
    assert library.connections[0].closed
    assert not keyfile.exists()
    assert not ssh._opened


def test_closing_keeps_the_other_connection_current(served, monkeypatch):
    library = FakeSSHLibrary()
    with fake_library(monkeypatch, library):
        keywords = SshKeywords()
        keywords.open_ssh_connection("ssh", alias="one")
        keywords.open_ssh_connection("ssh_pw", alias="two")
        library.switch_connection(1)
        keywords.close_ssh_connection(2)
        assert library.connections[1].closed
        assert library.current is library.connections[0]


def test_scope_end_closes_the_library_connection(served, monkeypatch):
    library = FakeSSHLibrary()
    with fake_library(monkeypatch, library):
        SshKeywords().open_ssh_connection("ssh")
        context.end_run()
    assert library.connections[0].closed
    assert not ssh._opened


def test_close_without_connection_fails(served, monkeypatch):
    with fake_library(monkeypatch, FakeSSHLibrary()), pytest.raises(Failure, match="no SSH connection is open"):
        SshKeywords().close_ssh_connection()


def test_split_options_rejects_unknown_names():
    library = FakeSSHLibrary()
    opening, login = split_options(library, {"prompt": "$", "delay": "1s"}, key_login=False)
    assert opening == {"prompt": "$"} and login == {"delay": "1s"}
    with pytest.raises(Failure, match="unknown option 'nope'.*prompt"):
        split_options(library, {"nope": 1}, key_login=True)


@dataclass
class Node:
    children: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)


class SSHMITM(Node):
    pass


def test_is_mitm_recognizes_the_proxy_shape():
    assert not is_mitm(Node())
    assert not is_mitm(Node(children={"tcp": Node()}))
    assert is_mitm(Node(children={"tcp": Node(children={"tcp": Node()})}))
    assert is_mitm(Node(children={"tcp": SSHMITM()}))


def test_mitm_endpoint_logs_a_warning(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(ssh.robot_logger, "warn", warnings.append)
    library = FakeSSHLibrary()
    endpoint = SshEndpoint("dut.ssh", "127.0.0.1", 2222, USERNAME, None, mitm=True)
    index, client = ssh._connect(library, endpoint, None, None, None, {})
    assert index == 1 and client is library.current
    assert len(warnings) == 1
    assert "ssh-mitm" in warnings[0] and "Put File" in warnings[0] and "Read Until Prompt" in warnings[0]
    # no key on the client: SSHLibrary logs in without a password, which ssh-mitm accepts
    assert library.calls[1][:3] == ("login", USERNAME, None)


def test_mitm_suite_through_the_proxy(server, tmp_path):
    """The real ssh-mitm driver in the middle: the key stays on the exporter side."""
    mitm = pytest.importorskip("jumpstarter_driver_ssh_mitm.driver")
    root = Composite(
        children={
            "ssh": SSHWrapper(
                children={
                    "tcp": mitm.SSHMITM(
                        children={"tcp": TcpNetwork(host="127.0.0.1", port=server.port)},
                        default_username=USERNAME,
                        ssh_identity=private_pem(server.client_key),
                    )
                },
                default_username=USERNAME,
                ssh_command=SSH_COMMAND,
            )
        }
    )
    with serve(root) as client, use_client(client):
        try:
            result = run_suite(
                tmp_path,
                """
*** Settings ***
Library    SSHLibrary    timeout=10s
Library    jumpstarter_robot.Jumpstarter

*** Test Cases ***
Through ssh-mitm
    Open SSH Connection    ssh    prompt=$
    Write    echo via-mitm
    ${out}=    Read Until Prompt
    Should Contain    ${out}    via-mitm
""",
            )
        finally:
            context.end_run()
            ssh._opened.clear()
    assert statuses(result) == {"Through ssh-mitm": "PASS"}
    warnings = [m.message or "" for m in result.errors.messages if m.level == "WARN"]
    assert any("ssh-mitm" in w for w in warnings), warnings
    assert "publickey" in server.logins


def test_extension_is_registered():
    from ..extensions import extensions_for

    class SSHWrapperClient:  # matched by name, like the real client
        pass

    words = [e.method_words for e in extensions_for(SSHWrapperClient)]
    assert "Connect" in words


def test_keyfile_permissions_are_not_world_readable(served):
    endpoint = SshKeywords().get_ssh_endpoint("ssh")
    mode = os.stat(endpoint.keyfile).st_mode
    assert not mode & (stat.S_IRWXG | stat.S_IRWXO)
