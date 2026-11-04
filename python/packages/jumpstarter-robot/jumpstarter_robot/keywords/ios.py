"""iOS devices and simulators: their transport, served locally for your own tools.

Jumpstarter does not wrap the iOS tool ecosystem. `Open iOS Device` serves
the device's transport on a loopback port (usbmux for a USB device, idb for
a simulator or a device with idb), and `Run iOS Tool` runs ``idevice*``,
``go-ios``, ``pymobiledevice3`` or ``idb`` with the environment that points
them at it (``USBMUXD_SOCKET_ADDRESS`` or ``IDB_COMPANION``), as ``j ios --``
does.

Open devices are kept in a connection cache: `Switch iOS Device` changes
the current one, and the other keywords act on it or on the one their
``alias`` names. Each stays open until `Close iOS Device`, the end of its
scope, or the session disconnecting.

The idb convenience keywords (`iOS Install App`, `iOS Screenshot` ...) need
``idb`` on PATH and fail with an install hint when it is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from robot.api import Failure
from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import ConnectionCache, DotDict, timestr_to_secs

from .. import context, sessions
from ..errors import mapped_errors
from ..extensions import register_node_extension
from .adb import output_file
from .connect import IOS_CLASSES, MatchedClient, child_path, describe, is_a, resolve_node, usable

IDB_HINT = (
    "{keyword} needs idb on PATH: install the client with `pip install fb-idb` "
    "(the companion runs on the exporter)"
)
ENV_BY_PROTOCOL = {"usbmux": "USBMUXD_SOCKET_ADDRESS", "idb": "IDB_COMPANION"}
# Seconds a stopped `idb log` gets to exit before it is killed.
STOP_GRACE = 5.0


@dataclass(eq=False)
class IosConnection:
    """An iOS device opened by `Open iOS Device`."""

    path: str
    session: str
    protocol: str
    endpoint: str
    node: MatchedClient
    stack: ExitStack
    close_scoped: Callable[[], None] = field(default=lambda: None)
    closed: bool = False

    @property
    def environment(self) -> dict[str, str]:
        return {ENV_BY_PROTOCOL[self.protocol]: self.endpoint}

    def close(self) -> None:
        self.close_scoped()


# Open devices. Module level, like the session registry, so every library
# instance (and the node shortcuts) share them.
_devices: ConnectionCache = ConnectionCache("No iOS device is open; use Open iOS Device first.")


def ios_node(path: str, session: str | None) -> tuple[sessions.Session, MatchedClient, str]:
    """The session, the ``IosDevice`` driver at ``path`` (or its ``ios`` child) and its path."""
    target, node = resolve_node(path, session)
    usable(node)
    if not is_a(node, IOS_CLASSES):
        child = (getattr(node, "children", {}) or {}).get("ios")
        if child is None or not is_a(child, IOS_CLASSES):
            raise Failure(f"driver '{path}' is {describe(node)}, not an iOS driver (IosDevice or iossimulator)")
        path, node = child_path(path, "ios"), usable(child)
    return target, node, path


def _choose_protocol(node: MatchedClient, protocol: str | None) -> str:
    """``protocol``, or the device's native one (the first it advertises)."""
    if protocol:
        return protocol
    supported = node.info().get("protocols") or []
    if not supported:
        raise Failure("the iOS device advertises no protocol")
    return supported[0]


def open_ios_device(
    path: str = "ios",
    protocol: str | None = None,
    alias: str | None = None,
    scope: str = "suite",
    session: str | None = None,
) -> int:
    """Serve the transport of the iOS driver at ``path``; returns its index in the cache."""
    with sessions.lock(), mapped_errors("Open iOS Device"):
        target, node, path = ios_node(path, session)
        stack = ExitStack()
        try:
            chosen = _choose_protocol(node, protocol)
            if chosen not in ENV_BY_PROTOCOL:
                raise Failure(f"protocol '{chosen}' is not supported; use one of {', '.join(ENV_BY_PROTOCOL)}")
            try:
                endpoint = stack.enter_context(node.serve(chosen))
            except (ValueError, RuntimeError) as e:  # unsupported protocol, device not present
                raise Failure(f"Open iOS Device: {e}") from e
        except BaseException:
            stack.close()
            raise
        connection = IosConnection(path, target.name, chosen, endpoint, node, stack)

        def close() -> None:
            connection.closed = True
            if _devices.current is connection:
                _devices.current_index = None
            stack.close()

        connection.close_scoped = context.add_scoped(close, scope, target)
        index = _devices.register(connection, alias)
    robot_logger.info(f"serving iOS '{path}' over {chosen} at {endpoint}")
    return index


def lookup(alias: str | int | None = None) -> IosConnection:
    """The open device ``alias`` names, or the current one."""
    try:
        connection = _devices.get_connection(alias) if alias not in (None, "") else _devices.current
    except RuntimeError as e:
        raise Failure(str(e)) from None
    if not connection:
        connection.raise_error()
    if connection.closed:
        raise Failure("that iOS device was closed; use Open iOS Device again")
    session = sessions.opened(connection.session)
    if session is None:
        raise Failure(f"the session of iOS device '{connection.path}' has disconnected")
    session.check_alive()
    return connection


def _tool_env(connection: IosConnection) -> dict[str, str]:
    env = os.environ.copy()
    env.update(connection.environment)
    return env


def run_tool(
    name: str, argv: list[str], env: dict[str, str], expected_rc: int | None, timeout: float | None = None
) -> DotDict:
    """Run a local tool, log it, and return ``DotDict(rc, stdout, stderr)``."""
    try:
        completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise Failure(f"{name}: '{argv[0]}' was not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise Failure(f"{name}: `{' '.join(argv)}` did not finish within {timeout} seconds") from None
    result = DotDict(rc=completed.returncode, stdout=_strip(completed.stdout), stderr=_strip(completed.stderr))
    robot_logger.info(f"$ {' '.join(argv)}\nrc={result.rc}\n{result.stdout}\n{result.stderr}".rstrip())
    if expected_rc is not None and result.rc != int(expected_rc):
        raise Failure(f"`{' '.join(argv)}` exited with {result.rc}, expected {expected_rc}: {result.stderr}")
    return result


def _strip(text: str | None) -> str:
    return (text or "").removesuffix("\n")


def _idb(keyword_name: str, alias: str | None) -> tuple[str, IosConnection]:
    """The idb binary and the device, which must be served over the idb protocol."""
    idb = shutil.which("idb")
    if idb is None:
        raise Failure(IDB_HINT.format(keyword=keyword_name))
    with sessions.lock(), mapped_errors(keyword_name):
        connection = lookup(alias)
    if connection.protocol != "idb":
        raise Failure(
            f"{keyword_name} needs the idb protocol, but '{connection.path}' is served over "
            f"{connection.protocol}; open it with `Open iOS Device    protocol=idb`"
        )
    return idb, connection


def _collect_for(argv: list[str], env: dict[str, str], seconds: float) -> str:
    """Run ``argv`` for ``seconds`` and return what it printed, then stop it."""
    try:
        process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        raise Failure(f"'{argv[0]}' was not found on PATH") from None
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    return output or ""


class IosKeywords:
    """iOS devices and simulators: transport endpoints, your own tools and idb helpers."""

    @keyword("Open iOS Device")
    def open_ios_device(
        self,
        path: str = "ios",
        protocol: str | None = None,
        alias: str | None = None,
        scope: str = "suite",
        session: str | None = None,
    ) -> int:
        """Serve the transport of the iOS device at ``path`` on a loopback port and make it current.

        ``protocol`` is ``usbmux`` (USB devices) or ``idb`` (simulators, and
        devices with idb); it defaults to the device's native protocol. A
        composite such as an iossimulator is used through its ``ios`` child,
        so ``path`` may name either.

        Returns the device's index; ``alias`` names it for the other iOS
        keywords. The endpoint closes with `Close iOS Device`, at the end of
        ``scope`` (``test``, ``suite`` or ``global``), or when the session
        disconnects.

        | Open iOS Device | simulator | alias=sim |
        | Run iOS Tool | idb | describe |
        | Open iOS Device | iphone.ios | protocol=usbmux |
        """
        return open_ios_device(path, protocol, alias, scope, session)

    @keyword("Switch iOS Device")
    def switch_ios_device(self, alias: str) -> int | None:
        """Make the iOS device with ``alias`` (or index) current; returns the previous index.

        | Switch iOS Device | sim |
        """
        previous = _devices.current_index
        _devices.current = lookup(alias)
        return previous

    @keyword("Close iOS Device")
    def close_ios_device(self, alias: str | None = None) -> None:
        """Stop serving the current iOS device, or the one with ``alias``, and its forwards.

        | Close iOS Device | sim |
        """
        with sessions.lock():
            lookup(alias).close()

    @keyword("Get iOS Environment")
    def get_ios_environment(self, alias: str | None = None) -> DotDict:
        """Return the environment that points iOS tools at the device.

        ``USBMUXD_SOCKET_ADDRESS`` for usbmux, ``IDB_COMPANION`` for idb, as
        ``j ios --`` sets them; pass it to the Process library.

        | &{env}= | Get iOS Environment |
        | Run Process | idevice_id | -n | env:USBMUXD_SOCKET_ADDRESS=${env.USBMUXD_SOCKET_ADDRESS} |
        """
        with sessions.lock(), mapped_errors("Get iOS Environment"):
            return DotDict(lookup(alias).environment)

    @keyword("Run iOS Tool")
    def run_ios_tool(
        self,
        command: str,
        *args: str,
        expected_rc: int | None = 0,
        timeout: str | None = None,
        alias: str | None = None,
    ) -> DotDict:
        """Run a local iOS tool against the device and return its result.

        Runs ``command`` with ``args`` and the device's environment (see `Get
        iOS Environment`) added to this process's own: ``idevice*``,
        ``go-ios``, ``pymobiledevice3`` or ``idb`` then reach the leased
        device. Returns a dictionary with ``rc``, ``stdout`` and ``stderr``
        (each without its trailing newline), logs them, and fails when
        ``rc`` is not ``expected_rc`` (``expected_rc=None`` accepts any).

        | ${r}= | Run iOS Tool | idb | describe | --json |
        | ${r}= | Run iOS Tool | ideviceinfo | -k | ProductVersion |
        | Run iOS Tool | idb | list-apps | timeout=1 min |
        """
        with sessions.lock(), mapped_errors("Run iOS Tool"):
            env = _tool_env(lookup(alias))
        # The tool reaches the device through the forward; it does not use the
        # client, so the session lock is not held while it runs.
        return run_tool("Run iOS Tool", [command, *args], env, expected_rc, _seconds(timeout))

    @keyword("iOS Forward Port")
    def ios_forward_port(self, port: int, local_port: int | None = None, alias: str | None = None) -> str:
        """Forward the device's TCP ``port`` to loopback; returns ``host:port``.

        Only the ``forward_ports`` the exporter configures can be forwarded.
        ``local_port`` defaults to the same number; ``0`` picks a free one.
        The forward closes with the device.

        | ${addr}= | iOS Forward Port | 8100 | local_port=0 |
        """
        with sessions.lock(), mapped_errors("iOS Forward Port"):
            connection = lookup(alias)
            try:
                return connection.stack.enter_context(connection.node.forward(int(port), local_port=local_port))
            except ValueError as e:  # not a configured port
                raise Failure(f"iOS Forward Port: {e}") from None

    @keyword("Get iOS HTTPS Endpoint")
    def get_ios_https_endpoint(self, alias: str | None = None) -> DotDict:
        """Serve the device's HTTPS service locally; returns ``url``, ``ca_file`` and ``metadata``.

        ``ca_file`` is the service's public CA, for clients that verify TLS.
        The endpoint closes with the device.

        | ${https}= | Get iOS HTTPS Endpoint |
        | GET | ${https.url}/status | verify=${https.ca_file} |
        """
        with sessions.lock(), mapped_errors("Get iOS HTTPS Endpoint"):
            connection = lookup(alias)
            try:
                endpoint = connection.stack.enter_context(connection.node.https())
            except (ValueError, TypeError) as e:  # no HTTPS service, bad metadata
                raise Failure(f"Get iOS HTTPS Endpoint: {e}") from None
            return DotDict(url=endpoint.url, ca_file=str(endpoint.ca_file), metadata=endpoint.metadata)

    @keyword("iOS Device Info")
    def ios_device_info(self, alias: str | None = None) -> DotDict:
        """Return the device's identity, presence, protocols and forward ports.

        | ${info}= | iOS Device Info |
        | Should Be True | ${info.present} |
        """
        with sessions.lock(), mapped_errors("iOS Device Info"):
            return context.to_robot(lookup(alias).node.info())

    @keyword("iOS Install App")
    def ios_install_app(self, app: str, alias: str | None = None) -> DotDict:
        """Install the ``.app`` or ``.ipa`` at ``app`` with ``idb install``.

        | iOS Install App | ${CURDIR}/MyApp.app |
        """
        idb, connection = _idb("iOS Install App", alias)
        if not Path(app).exists():
            raise Failure(f"app '{app}' does not exist")
        return run_tool("iOS Install App", [idb, "install", str(app)], _tool_env(connection), 0)

    @keyword("iOS Launch App")
    def ios_launch_app(self, bundle_id: str, *args: str, alias: str | None = None) -> DotDict:
        """Launch the app ``bundle_id`` with ``idb launch``; ``args`` go to the app.

        | iOS Launch App | com.example.MyApp |
        """
        idb, connection = _idb("iOS Launch App", alias)
        return run_tool("iOS Launch App", [idb, "launch", bundle_id, *args], _tool_env(connection), 0)

    @keyword("iOS Terminate App")
    def ios_terminate_app(self, bundle_id: str, alias: str | None = None) -> DotDict:
        """Stop the app ``bundle_id`` with ``idb terminate``.

        | iOS Terminate App | com.example.MyApp |
        """
        idb, connection = _idb("iOS Terminate App", alias)
        return run_tool("iOS Terminate App", [idb, "terminate", bundle_id], _tool_env(connection), 0)

    @keyword("iOS Screenshot")
    def ios_screenshot(self, filename: str | None = None, alias: str | None = None) -> str:
        """Take a screenshot with ``idb screenshot``, embed it in the log; returns its path.

        ``filename`` is relative to ``${OUTPUT DIR}/jumpstarter``, where a
        numbered file is created by default. idb needs the simulator's display:
        with recent Xcode it fails on a headless simulator ("Display has no
        activity"); AppiumLibrary's ``Capture Page Screenshot`` works there.

        | iOS Screenshot |
        """
        idb, connection = _idb("iOS Screenshot", alias)
        path = output_file(filename, "ios-screenshot", ".png")
        run_tool("iOS Screenshot", [idb, "screenshot", str(path)], _tool_env(connection), 0)
        if not path.is_file():
            raise Failure(f"iOS Screenshot: idb did not write {path}")
        context.embed_image(path)
        return str(path)

    @keyword("iOS Syslog")
    def ios_syslog(self, duration: str = "5s", alias: str | None = None) -> str:
        """Collect the device log with ``idb log`` for ``duration``; returns and logs it.

        | ${log}= | iOS Syslog | duration=10s |
        | Should Contain | ${log} | MyApp |
        """
        idb, connection = _idb("iOS Syslog", alias)
        output = _collect_for([idb, "log"], _tool_env(connection), _seconds(duration) or 0.0)
        robot_logger.info(output)
        return output


def _seconds(value: str | float | None) -> float | None:
    return None if value is None or value == "" else timestr_to_secs(value)


def _connect_extension(
    path: str,
    session: str | None,
    protocol: str | None = None,
    alias: str | None = None,
    scope: str = "suite",
) -> int:
    """Serve this iOS device's transport and make it current; see `Open iOS Device`.

    | Simulator Ios Connect |
    | Run iOS Tool | idb | describe |
    """
    return open_ios_device(path, protocol, alias, scope, session)


register_node_extension(set(IOS_CLASSES), "Connect", _connect_extension, tags=("ios",))
