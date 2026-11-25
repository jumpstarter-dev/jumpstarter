"""Android devices over ADB, through a Jumpstarter tunnel.

Two kinds of driver lead to an Android device, and `Open ADB Device` handles
both:

- an **ADB server** endpoint (``AdbServer``, and androidemulator's ``adb``
  child): the exporter's ADB server is forwarded to a local port and
  adbutils talks to it directly, so no local ``adb`` binary is needed. This
  is what ``AndroidEmulatorClient.adb_device()`` does.
- an **adbd** endpoint (``AdbDevice``): the device's adbd is forwarded and
  added to the local ADB server with ``adb connect``, which needs the
  ``adb`` binary; adbutils then drives that local server.

Open devices are kept in a connection cache, like SSHLibrary's: `Switch ADB
Device` changes the current one, and the other keywords act on it. Each
stays open until `Close ADB Device`, the end of its scope, or the session
disconnecting.

adbutils is optional (``pip install jumpstarter-robot[android]``) and is
imported only when a keyword runs.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from robot.api import Failure
from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import ConnectionCache, timestr_to_secs

from .. import context, sessions
from ..errors import mapped_errors
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient

CLIENT_LABEL = "jumpstarter.dev/client"

# A driver client recognized by class name rather than isinstance, because its
# driver package is an optional dependency that is never imported here. Its
# driver-specific methods (forward_adb, connect) are called untyped.
MatchedClient = Any

ADB_SERVER_CLASSES = frozenset({"AdbClient"})
ADB_DEVICE_CLASSES = frozenset({"AdbDeviceClient"})
ADB_COMPOSITE_CLASSES = frozenset({"AndroidEmulatorClient"})
ADB_CLASSES = ADB_SERVER_CLASSES | ADB_DEVICE_CLASSES | ADB_COMPOSITE_CLASSES


def class_names(node: Any) -> set[str]:
    """The class names in the MRO of ``node``'s client class.

    A stub (the client package is not installed) contributes the class name
    from its ``jumpstarter.dev/client`` label, so it is still recognized and
    fails with the stub's install hint when used.
    """
    names = {klass.__name__ for klass in type(node).__mro__}
    if isinstance(node, StubDriverClient):
        label = getattr(node, "labels", {}).get(CLIENT_LABEL, "")
        if label:
            names.add(label.rsplit(".", 1)[-1])
    return names


def is_a(node: Any, names: frozenset[str] | set[str]) -> bool:
    return bool(class_names(node) & names)


def resolve_node(path: str, session: str | None) -> tuple[sessions.Session, DriverClient]:
    """The session and the driver at ``path``; call with ``sessions.lock()`` held."""
    target = context.session(session)
    target.check_alive()
    try:
        node = target.resolve(path)
    except KeyError as e:
        raise Failure(e.args[0]) from None
    return target, node


def usable(node: DriverClient) -> DriverClient:
    """``node`` itself, or the stub's install error when its client package is missing."""
    if isinstance(node, StubDriverClient):
        node._raise_missing_error()
    return node


def describe(node: Any) -> str:
    labels = getattr(node, "labels", {}) or {}
    return labels.get(CLIENT_LABEL) or f"{type(node).__module__}.{type(node).__qualname__}"


ADBUTILS_HINT = "ADB keywords need adbutils: pip install jumpstarter-robot[android]"
ADB_BINARY_HINT = (
    "driver '{path}' is an adbd endpoint (AdbDevice), which is added to the local ADB server with "
    "`adb connect`, but no `adb` binary is on PATH: install the Android platform tools"
)
# Seconds between polls while waiting for a device or for it to boot.
POLL_INTERVAL = 2.0
REMOTE_TMP = "/data/local/tmp"


@dataclass(eq=False)
class AdbConnection:
    """An Android device opened by `Open ADB Device`."""

    path: str
    session: str
    kind: str  # server | device
    host: str
    port: int
    serial: str
    device: Any  # adbutils.AdbDevice
    close_scoped: Callable[[], None] = field(default=lambda: None)
    closed: bool = False

    def close(self) -> None:
        self.close_scoped()


# Open devices; the keywords act on the current one. Module level, like the
# session registry, so every library instance (and the node shortcuts) share them.
_devices: ConnectionCache = ConnectionCache("No ADB device is open; use Open ADB Device first.")


def adbutils() -> Any:
    """Import adbutils, failing with the install hint when it is missing."""
    try:
        import adbutils as module
    except ImportError:
        raise Failure(ADBUTILS_HINT) from None
    return module


def adb_node(path: str, session: str | None) -> tuple[sessions.Session, MatchedClient, str, str]:
    """The session, the ADB driver, its kind (``server`` or ``device``) and its path.

    An androidemulator node, or any composite with an ``adb`` child that is an
    ADB driver, resolves to that child.
    """
    target, node = resolve_node(path, session)
    usable(node)
    child = (getattr(node, "children", {}) or {}).get("adb")
    composite = is_a(node, ADB_COMPOSITE_CLASSES) or not is_a(node, ADB_CLASSES)
    if composite and child is not None and is_a(child, ADB_SERVER_CLASSES | ADB_DEVICE_CLASSES):
        path, node = (f"{path}.adb" if path else "adb"), usable(child)
    if is_a(node, ADB_SERVER_CLASSES):
        return target, node, "server", path
    if is_a(node, ADB_DEVICE_CLASSES):
        return target, node, "device", path
    raise Failure(
        f"driver '{path}' is {describe(node)}, not an ADB driver (AdbServer, AdbDevice or androidemulator)"
    )


@contextlib.contextmanager
def adb_errors(keyword_name: str) -> Generator[None]:
    """``mapped_errors``, plus adbutils' own errors and timeouts as failures."""
    with mapped_errors(keyword_name):
        try:
            yield
        except Failure:
            raise
        except Exception as e:
            if type(e).__module__.split(".")[0] == "adbutils" or isinstance(e, OSError):
                raise Failure(f"{keyword_name}: {e}") from e
            raise


def _seconds(value: str | float | None, default: float | None = None) -> float | None:
    return default if value is None or value == "" else timestr_to_secs(value)


def _wait_for_device(client: Any, serial: str | None, deadline: float) -> Any:
    """Poll the forwarded ADB server until it lists ``serial`` (or any device) as ready."""
    while True:
        with contextlib.suppress(Exception):
            devices = client.device_list()
            for device in devices:
                if serial is None or device.serial == serial:
                    return device
        if time.monotonic() >= deadline:
            wanted = f"device '{serial}'" if serial else "any device"
            raise Failure(f"the exporter's ADB server did not list {wanted} in time")
        time.sleep(POLL_INTERVAL)


def wait_until_booted(device: Any, deadline: float) -> None:
    """Poll ``sys.boot_completed`` until it is ``1``."""
    while True:
        with contextlib.suppress(Exception):
            if device.getprop("sys.boot_completed").strip() == "1":
                return
        if time.monotonic() >= deadline:
            raise Failure(f"ADB device '{device.serial}' did not finish booting in time")
        time.sleep(POLL_INTERVAL)


def _attach_server(node: MatchedClient, serial: str | None, deadline: float, stack: ExitStack) -> tuple:
    host, port = stack.enter_context(node.forward_adb())
    client = adbutils().AdbClient(host=host, port=int(port))
    device = _wait_for_device(client, serial, deadline)
    return host, int(port), device


def _attach_device(path: str, node: MatchedClient, serial: str | None, stack: ExitStack) -> tuple:
    adb = shutil.which("adb")
    if adb is None:
        raise Failure(ADB_BINARY_HINT.format(path=path))
    module = adbutils()
    address = stack.enter_context(node.connect(adb=adb))
    host, _, port = address.rpartition(":")
    # adbutils talks to the local server that `adb connect` just used.
    device = module.AdbClient().device(serial=serial or address)
    return host, int(port), device


def open_adb_device(
    path: str,
    alias: str | None = None,
    serial: str | None = None,
    timeout: str | float = "180s",
    wait_for_boot: bool = True,
    scope: str = "suite",
    session: str | None = None,
) -> int:
    """Open the Android device behind the ADB driver at ``path``; returns its index in the cache."""
    deadline = time.monotonic() + (_seconds(timeout, 180.0) or 0.0)
    with sessions.lock(), adb_errors("Open ADB Device"):
        scope = context.check_scope(scope)  # before anything is opened
        target, node, kind, path = adb_node(path, session)
        stack = ExitStack()
        try:
            if kind == "server":
                host, port, device = _attach_server(node, serial, deadline, stack)
            else:
                host, port, device = _attach_device(path, node, serial, stack)
            if wait_for_boot:
                wait_until_booted(device, deadline)
        except BaseException:
            stack.close()
            raise
        connection = AdbConnection(path, target.name, kind, host, port, device.serial, device)

        def close() -> None:
            connection.closed = True
            if _devices.current is connection:
                _devices.current_index = None
            stack.close()

        connection.close_scoped = context.add_scoped(close, scope, target)
        index = _devices.register(connection, alias)
    robot_logger.info(f"opened ADB device {device.serial} through '{path}' ({kind} endpoint {host}:{port})")
    return index


def current() -> AdbConnection:
    connection = _devices.current
    if not connection:
        connection.raise_error()
    if connection.closed:
        raise Failure("the current ADB device was closed; use Open ADB Device or Switch ADB Device")
    session = sessions.opened(connection.session)
    if session is None:
        raise Failure(f"the session of ADB device '{connection.serial}' has disconnected")
    session.check_alive()
    return connection


class AdbKeywords:
    """Android devices over ADB: the exporter's ADB server or a device's adbd."""

    @keyword("Open ADB Device")
    def open_adb_device(
        self,
        path: str,
        alias: str | None = None,
        serial: str | None = None,
        timeout: str = "180s",
        wait_for_boot: bool = True,
        scope: str = "suite",
        session: str | None = None,
    ) -> int:
        """Open the Android device behind the ADB driver at ``path`` and make it current.

        For an ADB server (``AdbServer``, or an androidemulator, whose
        ``adb`` child is used) the exporter's server is forwarded and used
        directly; no local ``adb`` is needed. ``serial`` picks a device when
        the server has several, and the keyword waits up to ``timeout`` for
        it to appear, as it does after an emulator is powered on.

        For a device's adbd (``AdbDevice``) the forward is added to the
        local ADB server with ``adb connect``, which needs ``adb`` on PATH,
        and is disconnected again on close.

        With ``wait_for_boot`` the keyword then waits, within the same
        ``timeout``, until ``sys.boot_completed`` is ``1``. Returns the
        device's index; ``alias`` names it for `Switch ADB Device` and `Close
        ADB Device`. The device closes at the end of ``scope`` (``test``,
        ``suite`` or ``global``) or when the session disconnects.

        Needs adbutils (``pip install jumpstarter-robot[android]``).

        | Power On | # the emulator's power keyword |
        | Open ADB Device | android | alias=phone | timeout=5 min |
        | ${model}= | ADB Shell | getprop ro.product.model |
        """
        return open_adb_device(path, alias, serial, timeout, wait_for_boot, scope, session)

    @keyword("Switch ADB Device")
    def switch_adb_device(self, alias: str) -> int | None:
        """Make the ADB device with ``alias`` (or index) current; returns the previous index.

        | ${previous}= | Switch ADB Device | tablet |
        | ADB Shell | input keyevent KEYCODE_HOME |
        | Switch ADB Device | ${previous} |
        """
        previous = _devices.current_index
        try:
            connection = _devices.get_connection(alias)
        except RuntimeError as e:
            raise Failure(str(e)) from None
        if connection.closed:
            raise Failure(f"ADB device '{alias}' was closed")
        _devices.current = connection
        return previous

    @keyword("Close ADB Device")
    def close_adb_device(self, alias: str | None = None) -> None:
        """Close the current ADB device, or the one with ``alias``, and its forward.

        | Close ADB Device | phone |
        """
        try:
            connection = _devices.get_connection(alias) if alias is not None else _devices.current
        except RuntimeError as e:
            raise Failure(str(e)) from None
        if not connection:
            connection.raise_error()
        with sessions.lock():
            connection.close()

    @keyword("Close All ADB Devices")
    def close_all_adb_devices(self) -> None:
        """Close every ADB device opened by `Open ADB Device`, for a suite teardown.

        | [Teardown] | Close All ADB Devices |
        """
        with sessions.lock():
            for connection in list(_devices):
                connection.close()
            _devices.empty_cache()

    @keyword("ADB Shell")
    def adb_shell(self, command: str, timeout: str | None = None, expected_rc: int | None = 0) -> str:
        """Run ``command`` in the device's shell and return its output.

        The output is logged; the trailing newline is removed. Fails when the
        exit status is not ``expected_rc`` (``expected_rc=None`` accepts
        any). ``timeout`` bounds the command, for example ``30s``.

        | ${model}= | ADB Shell | getprop ro.product.model |
        | ADB Shell | pm list packages | timeout=1 min |
        | ${out}= | ADB Shell | ls /missing | expected_rc=None |
        """
        with sessions.lock(), adb_errors("ADB Shell"):
            device = current().device
            seconds = _seconds(timeout)
            kwargs = {"timeout": seconds} if seconds is not None else {}
            result = device.shell2(command, rstrip=True, **kwargs)
        output = result.output
        robot_logger.info(f"rc={result.returncode}\n{output}")
        if expected_rc is not None and result.returncode != int(expected_rc):
            raise Failure(f"`{command}` exited with {result.returncode}, expected {expected_rc}: {output}")
        return output

    @keyword("ADB Install")
    def adb_install(self, apk: str, replace: bool = True, grant_permissions: bool = True) -> None:
        """Install the APK file ``apk`` on the device.

        ``replace`` reinstalls over an existing version (``-r``);
        ``grant_permissions`` grants its runtime permissions (``-g``). Test
        packages are allowed.

        | ADB Install | ${CURDIR}/app-debug.apk |
        | ADB Install | old.apk | replace=False |
        """
        local = Path(apk)
        if not local.is_file():
            raise Failure(f"APK '{apk}' does not exist")
        flags = (["-r"] if replace else []) + ["-t"] + (["-g"] if grant_permissions else [])
        with sessions.lock(), adb_errors("ADB Install"):
            device = current().device
            remote = f"{REMOTE_TMP}/{local.name}"
            device.sync.push(str(local), remote)
            device.install_remote(remote, clean=True, flags=flags)
        robot_logger.info(f"installed {local.name}")

    @keyword("ADB Uninstall")
    def adb_uninstall(self, package: str) -> None:
        """Uninstall ``package`` from the device; fails if it is not installed.

        | ADB Uninstall | com.example.app |
        """
        with sessions.lock(), adb_errors("ADB Uninstall"):
            output = str(current().device.uninstall(package) or "").strip()
        if "Success" not in output:
            raise Failure(f"uninstalling {package} failed: {output or 'no output'}")

    @keyword("ADB Push")
    def adb_push(self, local: str, remote: str) -> None:
        """Copy the local file ``local`` to ``remote`` on the device.

        | ADB Push | ${CURDIR}/data.json | /sdcard/Download/data.json |
        """
        if not Path(local).is_file():
            raise Failure(f"local file '{local}' does not exist")
        with sessions.lock(), adb_errors("ADB Push"):
            current().device.sync.push(str(local), remote)

    @keyword("ADB Pull")
    def adb_pull(self, remote: str, local: str | None = None) -> str:
        """Copy ``remote`` from the device to ``local``; returns the local path.

        ``local`` defaults to a file under ``${OUTPUT DIR}/jumpstarter``.

        | ${path}= | ADB Pull | /sdcard/Download/result.txt |
        | ADB Pull | /sdcard/trace.txt | ${OUTPUT DIR}/trace.txt |
        """
        name = Path(remote).name or "pulled"
        target = Path(local) if local else context.artifact_path(Path(name).stem, Path(name).suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sessions.lock(), adb_errors("ADB Pull"):
            current().device.sync.pull(remote, str(target))
        return str(target)


    @keyword("ADB Get Property")
    def adb_get_property(self, name: str) -> str:
        """Return the system property ``name`` (``getprop``); empty when unset.

        | ${sdk}= | ADB Get Property | ro.build.version.sdk |
        """
        with sessions.lock(), adb_errors("ADB Get Property"):
            return current().device.getprop(name)

    @keyword("ADB Wait For Boot")
    def adb_wait_for_boot(self, timeout: str = "180s") -> None:
        """Wait until the current device reports ``sys.boot_completed`` as ``1``.

        | ADB Wait For Boot | timeout=5 min |
        """
        deadline = time.monotonic() + (_seconds(timeout, 180.0) or 0.0)
        with sessions.lock(), adb_errors("ADB Wait For Boot"):
            wait_until_booted(current().device, deadline)
