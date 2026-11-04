"""Linux devices over SSH: SSHLibrary through a Jumpstarter tunnel.

`Open SSH Connection` does what ``j ssh`` does (a local port forwarded to the
``SSHWrapper`` driver's ``tcp`` child, and the exporter's identity written to
a private key file) and then hands the endpoint to SSHLibrary. From there on
every SSHLibrary keyword works on the leased device: ``Execute Command``,
``Write``/``Read Until``, ``Put File``, ``Get File`` and the rest.

The forward and the key file stay open until `Close SSH Connection`, the end
of the keyword's scope, or the session disconnecting, whichever comes first.

SSHLibrary is optional (``pip install jumpstarter-robot[ssh]``); it is found
as the ``SSHLibrary`` library the suite imports, never imported here.
`Execute Over SSH` needs no library at all, only a local ``ssh`` binary.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from robot.api import Failure
from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import DotDict, is_truthy

from .. import context, sessions
from ..errors import mapped_errors
from ..extensions import register_node_extension
from .connect import SSH_CLASSES, MatchedClient, describe, is_a, resolve_node, usable

SSH_LIBRARY_HINT = (
    "Open SSH Connection needs SSHLibrary imported in the suite: add `Library    SSHLibrary` to its settings "
    "(install it with `pip install jumpstarter-robot[ssh]`)"
)
MITM_WARNING = (
    "driver '{path}' reaches its device through ssh-mitm, which proxies a single channel per connection and "
    "neither SFTP nor port forwarding. SSHLibrary's login uses that channel for its interactive shell, so use "
    "Write, Read and Read Until Prompt on this connection; Execute Command, Start Command, Put File, Get File and "
    "Create Local SSH Tunnel do not work and Execute Command hangs. For one-off commands use Execute Over SSH."
)
# Parameters of SSHLibrary's methods this module fills in itself.
_OWN_OPEN = {"self", "host", "alias", "port"}
_OWN_LOGIN = {"self", "username", "password", "keyfile"}


@dataclass(eq=False)
class SshEndpoint:
    """A local address forwarded to a device's SSH server, with what it takes to log in."""

    path: str
    host: str
    port: int
    username: str
    keyfile: str | None
    mitm: bool
    close: Callable[[], None] = field(default=lambda: None)

    def as_robot(self) -> DotDict:
        return DotDict(host=self.host, port=self.port, username=self.username, keyfile=self.keyfile)


# SSHLibrary connections opened here: id(SSHLibrary client) → closer. Module
# level, like the session registry, so every library instance sees them.
_opened: dict[int, Callable[[], None]] = {}


def sshlibrary() -> Any:
    """The suite's SSHLibrary instance; tests replace this function."""
    from robot.libraries.BuiltIn import BuiltIn, RobotNotRunningError

    try:
        return BuiltIn().get_library_instance("SSHLibrary")
    except (RuntimeError, RobotNotRunningError):
        raise Failure(SSH_LIBRARY_HINT) from None


def ssh_node(path: str, session: str | None) -> tuple[sessions.Session, MatchedClient]:
    """The session and the ``SSHWrapper`` driver at ``path``."""
    target, node = resolve_node(path, session)
    usable(node)
    if not is_a(node, SSH_CLASSES):
        raise Failure(f"driver '{path}' is {describe(node)}, not an SSH driver (jumpstarter_driver_ssh SSHWrapper)")
    return target, node


def is_mitm(node: MatchedClient) -> bool:
    """Whether the ``tcp`` child of an ``SSHWrapper`` is an ssh-mitm proxy.

    ``SSHMITM`` reports the generic network client, like ``TcpNetwork``, so it
    is recognized by its shape: it is the network child that has its own
    ``tcp`` child (the device's address).
    """
    tcp = (getattr(node, "children", {}) or {}).get("tcp")
    if tcp is None:
        return False
    return "SSHMITM" in {k.__name__ for k in type(tcp).__mro__} or "tcp" in (getattr(tcp, "children", {}) or {})


def write_identity(identity: str, stack: ExitStack) -> str:
    """Write ``identity`` to a mode-0600 file in a private directory that ``stack`` removes."""
    directory = tempfile.mkdtemp(prefix="jumpstarter-ssh-")  # mode 0700
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    keyfile = os.path.join(directory, "id")
    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(identity if identity.endswith("\n") else identity + "\n")
    return keyfile


def _enter_endpoint(path: str, node: MatchedClient, stack: ExitStack) -> SshEndpoint:
    """Forward a local port to ``node`` and write its identity, both closed with ``stack``."""
    from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

    host, port = stack.enter_context(TcpPortforwardAdapter(client=node))
    identity = node.identity
    keyfile = write_identity(identity, stack) if identity else None
    robot_logger.info(f"forwarded {host}:{port} to SSH driver '{path}'")
    return SshEndpoint(path, host, int(port), node.username or "", keyfile, is_mitm(node))


def open_endpoint(path: str, session: str | None, scope: str = "suite") -> SshEndpoint:
    """Forward a local port to the SSH driver at ``path`` and write its identity, for ``scope``."""
    with sessions.lock():
        target, node = ssh_node(path, session)
        stack = ExitStack()
        try:
            endpoint = _enter_endpoint(path, node, stack)
        except BaseException:
            stack.close()
            raise
        endpoint.close = context.add_scoped(stack.close, scope, target)
        return endpoint


def _option_names(method: Callable[..., Any], own: set[str]) -> dict[str, inspect.Parameter]:
    return {name: p for name, p in inspect.signature(method).parameters.items() if name not in own}


def _convert(parameter: inspect.Parameter, value: Any) -> Any:
    # SSHLibrary's methods are called directly, so Robot's argument conversion
    # does not run: turn 'False' into False where SSHLibrary expects a bool.
    if isinstance(parameter.default, bool) and isinstance(value, str):
        return is_truthy(value)
    return value


def split_options(library: Any, options: dict[str, Any], key_login: bool) -> tuple[dict, dict]:
    """Split keyword options into SSHLibrary ``open_connection`` and login arguments."""
    opening = _option_names(library.open_connection, _OWN_OPEN)
    login = _option_names(library.login_with_public_key if key_login else library.login, _OWN_LOGIN)
    open_args: dict[str, Any] = {}
    login_args: dict[str, Any] = {}
    for name, value in options.items():
        if name in opening:
            open_args[name] = _convert(opening[name], value)
        elif name in login:
            login_args[name] = _convert(login[name], value)
        else:
            accepted = ", ".join(sorted(set(opening) | set(login)))
            raise Failure(f"Open SSH Connection got unknown option '{name}'; SSHLibrary accepts: {accepted}")
    return open_args, login_args


def _close_library_connection(library: Any, client: Any) -> None:
    """Close one SSHLibrary connection, keeping whichever other one was current.

    The connection object is closed rather than an index, because SSHLibrary
    reuses indices after ``Close All Connections``.
    """
    previous = library.current
    try:
        library.switch_connection(client.config.index)
        if library.current is client:
            library.close_connection()
        else:
            client.close()
    except Exception:  # noqa: BLE001 - already closed through SSHLibrary
        client.close()
    if previous and previous is not client:
        with contextlib.suppress(Exception):  # the previous one went away meanwhile
            library.switch_connection(previous.config.index)


def _login(library: Any, endpoint: SshEndpoint, username: str, password: str | None, login_args: dict) -> None:
    if endpoint.keyfile and password is None:
        library.login_with_public_key(username, endpoint.keyfile, **login_args)
    else:
        library.login(username, password, **login_args)


def _connect(library: Any, endpoint: SshEndpoint, alias, password, username, options) -> tuple[int, Any]:
    """Open and log in an SSHLibrary connection to ``endpoint``; returns its index and client."""
    if endpoint.mitm:
        robot_logger.warn(MITM_WARNING.format(path=endpoint.path))
    user = username or endpoint.username
    if not user:
        raise Failure(f"SSH driver '{endpoint.path}' has no default_username; pass username=")
    key_login = bool(endpoint.keyfile) and password is None
    open_args, login_args = split_options(library, options, key_login)
    index = library.open_connection(endpoint.host, alias=alias, port=endpoint.port, **open_args)
    client = library.current
    try:
        _login(library, endpoint, user, password, login_args)
    except BaseException:
        _close_library_connection(library, client)
        raise
    return index, client


def open_ssh_connection(
    path: str = "ssh",
    alias: str | None = None,
    password: str | None = None,
    username: str | None = None,
    scope: str = "suite",
    session: str | None = None,
    **options: Any,
) -> int:
    """Open an SSHLibrary connection to the SSH driver at ``path``; returns its index."""
    # An unset variable passed through ${EMPTY} must not replace key login
    # with an empty password.
    password = password or None
    username = username or None
    library = sshlibrary()
    with sessions.lock(), mapped_errors("Open SSH Connection"):
        target, node = ssh_node(path, session)
        stack = ExitStack()
        try:
            endpoint = _enter_endpoint(path, node, stack)
            index, client = _connect(library, endpoint, alias, password, username, options)
        except BaseException:
            stack.close()
            raise

        def close() -> None:
            _opened.pop(id(client), None)
            try:
                _close_library_connection(library, client)
            finally:
                stack.close()

        # Closes the SSHLibrary connection before the forward under it, at the
        # end of the scope or before the session disconnects.
        _opened[id(client)] = context.add_scoped(close, scope, target)
        return index


def close_ssh_connection(alias: str | int | None = None) -> None:
    """Close an SSHLibrary connection opened by `Open SSH Connection` and its forward.

    Closing another connection than the current one leaves the current one current.
    """
    library = sshlibrary()
    current = library.current
    if alias is not None and str(alias) != "":
        library.switch_connection(alias)
    client = library.current
    if not client:
        raise Failure("no SSH connection is open" if alias is None else f"SSH connection '{alias}' is not open")
    closer = _opened.get(id(client))
    with sessions.lock():
        if closer is not None:
            closer()
        else:
            library.close_connection()
    if current and current is not client:
        with contextlib.suppress(Exception):  # it went away meanwhile
            library.switch_connection(current.config.index)


class SshKeywords:
    """Linux devices over SSH, through SSHLibrary or the local ``ssh`` binary."""

    @keyword("Open SSH Connection")
    def open_ssh_connection(
        self,
        path: str = "ssh",
        alias: str | None = None,
        password: str | None = None,
        username: str | None = None,
        scope: str = "suite",
        session: str | None = None,
        **sshlibrary_options: Any,
    ) -> int:
        """Open an SSHLibrary connection to the device behind the SSH driver at ``path``.

        Forwards a local port to the driver (as ``j ssh`` does), writes the
        exporter's identity to a mode-0600 key file in a private temporary
        directory, opens an SSHLibrary connection to the forwarded port and
        logs in: with the key when the exporter has one, otherwise with
        ``password`` (or none at all). ``username`` defaults to the driver's
        ``default_username``; a ``password`` given while a key is available
        logs in with the password instead.

        Every SSHLibrary keyword then works on the device. Returns the
        SSHLibrary connection index; ``alias`` names it for SSHLibrary's
        ``Switch Connection`` and for `Close SSH Connection`.

        ``sshlibrary_options`` go to SSHLibrary's ``Open Connection``
        (``timeout``, ``prompt``, ``newline``, ``term_type``, ``width``,
        ``encoding`` ...) or its login (``delay``, ``keep_alive_interval``,
        ``allow_agent`` ...), wherever the name belongs.

        The connection, the forward and the key file are closed by `Close
        SSH Connection`, at the end of ``scope`` (``test``, ``suite`` or
        ``global``) or when the session disconnects.

        When the driver's ``tcp`` child is ssh-mitm, the key stays on the
        exporter, and the proxy carries one channel per connection with no
        SFTP or port forwarding. SSHLibrary's login takes that channel for
        its interactive shell, so only ``Write``, ``Read`` and ``Read Until
        Prompt`` work; ``Execute Command`` would hang, and ``Put File``,
        ``Get File`` and tunnels fail. A warning says so. `Execute Over SSH`
        runs one-off commands through ssh-mitm fine.

        Needs ``Library    SSHLibrary`` in the suite
        (``pip install jumpstarter-robot[ssh]``).

        | Open SSH Connection | dut.ssh | alias=dut | prompt=$ |
        | ${out}= | Execute Command | uname -a |
        | Put File | app.tar.gz | /tmp/ |
        | Open SSH Connection | password=secret | username=root | scope=test |
        """
        return open_ssh_connection(path, alias, password, username, scope, session, **sshlibrary_options)

    @keyword("Close SSH Connection")
    def close_ssh_connection(self, alias: str | None = None) -> None:
        """Close an SSH connection opened by `Open SSH Connection`, then its forward.

        ``alias`` is the connection's alias or index; by default the current
        SSHLibrary connection is closed. A connection SSHLibrary opened
        itself is just closed.

        | Close SSH Connection | dut |
        """
        close_ssh_connection(alias)

    @keyword("Get SSH Endpoint")
    def get_ssh_endpoint(self, path: str = "ssh", scope: str = "suite", session: str | None = None) -> DotDict:
        """Forward a local port to the SSH driver at ``path`` and return how to log in.

        Returns a dictionary with ``host``, ``port``, ``username`` and
        ``keyfile`` (None when the exporter has no identity), for tools such
        as ``scp`` or ``rsync`` run with the Process library. The forward and
        the key file stay open until the end of ``scope`` or until the
        session disconnects.

        | ${ep}= | Get SSH Endpoint | dut.ssh |
        | Run Process | scp | -P | ${ep.port} | -i | ${ep.keyfile} | -o | StrictHostKeyChecking=no |
        | ... | build.img | ${ep.username}@${ep.host}:/tmp/ |
        """
        with sessions.lock(), mapped_errors("Get SSH Endpoint"):
            endpoint = open_endpoint(path, session, scope)
            if endpoint.mitm:
                robot_logger.warn(MITM_WARNING.format(path=path))
            return endpoint.as_robot()

    @keyword("Execute Over SSH")
    def execute_over_ssh(
        self, command: str, path: str = "ssh", expected_rc: int | None = 0, session: str | None = None
    ) -> DotDict:
        """Run ``command`` on the device with the local ``ssh`` binary, like ``j ssh``.

        Needs no Robot library, only ``ssh`` on PATH. Returns a dictionary
        with ``rc``, ``stdout`` and ``stderr`` (each without its trailing
        newline), and fails when ``rc`` is not
        ``expected_rc`` (pass ``expected_rc=None`` to accept any).

        | ${r}= | Execute Over SSH | cat /etc/os-release | path=dut.ssh |
        | Should Contain | ${r.stdout} | Fedora |
        | ${r}= | Execute Over SSH | systemctl is-active foo | expected_rc=None |
        """
        from jumpstarter_driver_ssh.client import SSHCommandRunOptions

        with sessions.lock(), mapped_errors("Execute Over SSH"):
            _, node = ssh_node(path, session)
            result = node.run(SSHCommandRunOptions(direct=False, capture_output=True, capture_as_text=True), [command])
        outcome = DotDict(rc=result.return_code, stdout=_text(result.stdout), stderr=_text(result.stderr))
        robot_logger.info(f"rc={outcome.rc}\n{outcome.stdout}\n{outcome.stderr}".rstrip())
        if expected_rc is not None and outcome.rc != int(expected_rc):
            raise Failure(
                f"`{command}` over SSH exited with {outcome.rc}, expected {expected_rc}: {outcome.stderr.strip()}"
            )
        return outcome


def _text(value: str | bytes | None) -> str:
    """Captured output as text, without its trailing newline (as the Process library does)."""
    text = value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
    return text.removesuffix("\n")


def _connect_extension(
    path: str,
    session: str | None,
    alias: str | None = None,
    password: str | None = None,
    username: str | None = None,
    scope: str = "suite",
    **sshlibrary_options: Any,
) -> int:
    """Open an SSHLibrary connection to this driver's device; see `Open SSH Connection`.

    Needs ``Library    SSHLibrary``. Returns the SSHLibrary connection index.

    | Dut Ssh Connect | alias=dut |
    | Execute Command | uname -a |
    """
    return open_ssh_connection(path, alias, password, username, scope, session, **sshlibrary_options)


register_node_extension(set(SSH_CLASSES), "Connect", _connect_extension, tags=("ssh",))
