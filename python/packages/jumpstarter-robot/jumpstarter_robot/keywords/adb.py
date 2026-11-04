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
from robot.utils import ConnectionCache, DotDict, timestr_to_secs

from .. import context, sessions
from ..errors import mapped_errors
from ..extensions import register_node_extension
from .connect import (
    ADB_CLASSES,
    ADB_COMPOSITE_CLASSES,
    ADB_DEVICE_CLASSES,
    ADB_SERVER_CLASSES,
    MatchedClient,
    child_path,
    describe,
    is_a,
    resolve_node,
    usable,
)

ADBUTILS_HINT = "ADB keywords need adbutils: pip install jumpstarter-robot[android]"
ADB_BINARY_HINT = (
    "driver '{path}' is an adbd endpoint (AdbDevice), which is added to the local ADB server with "
    "`adb connect`, but no `adb` binary is on PATH: install the Android platform tools"
)
# Seconds between polls while waiting for a device or for it to boot.
POLL_INTERVAL = 2.0
REMOTE_TMP = "/data/local/tmp"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
        path, node = child_path(path, "adb"), usable(child)
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


def output_file(filename: str | None, stem: str, suffix: str) -> Path:
    """``filename`` under ``${OUTPUT DIR}/jumpstarter`` (unless absolute), or a numbered file there."""
    if not filename:
        return context.artifact_path(stem, suffix).resolve()
    path = Path(filename)
    if not path.is_absolute():
        path = context.output_dir() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.resolve()


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
        target = Path(local) if local else output_file(None, Path(name).stem, Path(name).suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sessions.lock(), adb_errors("ADB Pull"):
            current().device.sync.pull(remote, str(target))
        return str(target)

    @keyword("ADB Logcat")
    def adb_logcat(self, lines: int = 200, clear: bool = False) -> str:
        """Return the last ``lines`` lines of the device log, and log them.

        With ``clear`` the log buffer is cleared afterwards, so the next call
        shows only what happened since.

        | ${log}= | ADB Logcat | lines=500 |
        | ADB Logcat | clear=True | # in a test setup, to start fresh |
        """
        with sessions.lock(), adb_errors("ADB Logcat"):
            device = current().device
            output = device.shell(["logcat", "-d", "-v", "time", "-t", str(int(lines))])
            if clear:
                device.shell(["logcat", "-c"])
        robot_logger.info(output)
        return output

    @keyword("ADB Screenshot")
    def adb_screenshot(self, filename: str | None = None) -> str:
        """Take a screenshot, save it as PNG and embed it in the log; returns its path.

        ``filename`` is relative to ``${OUTPUT DIR}/jumpstarter``, where a
        numbered file is created by default.

        | ADB Screenshot |
        | ${png}= | ADB Screenshot | login.png |
        """
        with sessions.lock(), adb_errors("ADB Screenshot"):
            data = current().device.shell(["screencap", "-p"], encoding=None)
        if not isinstance(data, (bytes, bytearray)) or not data.startswith(PNG_SIGNATURE):
            raise Failure("ADB Screenshot: the device did not return a PNG image")
        path = output_file(filename, "adb-screenshot", ".png")
        path.write_bytes(bytes(data))
        context.embed_image(path)
        return str(path)

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

    @keyword("ADB Reboot")
    def adb_reboot(self, wait: bool = False, timeout: str = "180s") -> None:
        """Reboot the current device; with ``wait``, wait until it has booted again.

        Waiting works for ADB server endpoints (emulators, ``AdbServer``):
        the device reappears on the same server. An adbd endpoint
        (``AdbDevice``) drops its connection on reboot; open it again.

        | ADB Reboot | wait=True | timeout=5 min |
        """
        deadline = time.monotonic() + (_seconds(timeout, 180.0) or 0.0)
        with sessions.lock(), adb_errors("ADB Reboot"):
            device = current().device
            with contextlib.suppress(Exception):  # the connection drops as the device goes down
                device.reboot()
            if wait:
                _wait_until_down(device, deadline)
                wait_until_booted(device, deadline)

    @keyword("Get ADB Environment")
    def get_adb_environment(self, path: str, scope: str = "suite", session: str | None = None) -> DotDict:
        """Forward the ADB driver at ``path`` and return the environment for your own tools.

        For an ADB server endpoint this is ``ANDROID_ADB_SERVER_ADDRESS`` and
        ``ANDROID_ADB_SERVER_PORT``, so a real ``adb``, ``gradle
        connectedCheck`` or ``maestro`` run with the Process library talks to
        the exporter's server. For an adbd endpoint (``AdbDevice``) the device
        is added to the local ADB server with ``adb connect`` and
        ``ANDROID_SERIAL`` names it. The forward stays open until the end of
        ``scope`` or until the session disconnects.

        | &{env}= | Get ADB Environment | android |
        | ${r}= | Run Process | adb | devices | env:ANDROID_ADB_SERVER_PORT=${env.ANDROID_ADB_SERVER_PORT} |
        | ... | env:ANDROID_ADB_SERVER_ADDRESS=${env.ANDROID_ADB_SERVER_ADDRESS} |
        """
        with sessions.lock(), adb_errors("Get ADB Environment"):
            target, node, kind, path = adb_node(path, session)
            stack = ExitStack()
            try:
                env = _environment(path, node, kind, stack)
            except BaseException:
                stack.close()
                raise
            context.add_scoped(stack.close, scope, target)
        return DotDict(env)


def _environment(path: str, node: MatchedClient, kind: str, stack: ExitStack) -> dict[str, str]:
    if kind == "server":
        host, port = stack.enter_context(node.forward_adb())
        return {"ANDROID_ADB_SERVER_ADDRESS": str(host), "ANDROID_ADB_SERVER_PORT": str(port)}
    adb = shutil.which("adb")
    if adb is None:
        raise Failure(ADB_BINARY_HINT.format(path=path))
    return {"ANDROID_SERIAL": stack.enter_context(node.connect(adb=adb))}


def _wait_until_down(device: Any, deadline: float) -> None:
    """Wait for a rebooting device to stop reporting a completed boot."""
    while time.monotonic() < deadline:
        try:
            if device.getprop("sys.boot_completed").strip() != "1":
                return
        except Exception:  # noqa: BLE001 - unreachable means it went down
            return
        time.sleep(POLL_INTERVAL)


def _connect_extension(
    path: str,
    session: str | None,
    alias: str | None = None,
    serial: str | None = None,
    timeout: str = "180s",
    wait_for_boot: bool = True,
    scope: str = "suite",
) -> int:
    """Open this driver's Android device and make it current; see `Open ADB Device`.

    Returns the device's index for `Switch ADB Device`.

    | Emulator Connect | timeout=5 min |
    | ADB Shell | getprop ro.product.model |
    """
    return open_adb_device(path, alias, serial, timeout, wait_for_boot, scope, session)


register_node_extension(set(ADB_CLASSES), "Connect", _connect_extension, tags=("adb",))
