"""`Connect To Device`: one keyword that opens a device's native control channel.

A leased device is reached over SSH (Linux), ADB (Android) or an iOS
transport, depending on which driver the exporter gives it. `Connect To
Device` looks at the driver's client class, or for composites such as
androidemulator and iossimulator at their ``adb``, ``ios`` and ``ssh``
children, and opens the matching connection with `Open SSH Connection`,
`Open ADB Device` or `Open iOS Device`.

The helpers here (resolving a path to a driver and recognizing its family by
class name) are shared by the SSH, ADB and iOS keyword sets. Classes are
matched by the names in their MRO, so no driver package is imported to
recognize one.
"""

from __future__ import annotations

from typing import Any

from robot.api import Failure
from robot.api.deco import keyword

from .. import context, sessions
from ..errors import mapped_errors
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient

CLIENT_LABEL = "jumpstarter.dev/client"

# A driver client recognized by class name (``is_a``) rather than isinstance,
# because its driver package is an optional dependency that is never imported
# here. Its driver-specific methods (forward_adb, serve, run...) are called
# untyped.
MatchedClient = Any

# Device family → client class names that belong to it.
SSH_CLASSES = frozenset({"SSHWrapperClient"})
ADB_SERVER_CLASSES = frozenset({"AdbClient"})
ADB_DEVICE_CLASSES = frozenset({"AdbDeviceClient"})
ADB_COMPOSITE_CLASSES = frozenset({"AndroidEmulatorClient"})
ADB_CLASSES = ADB_SERVER_CLASSES | ADB_DEVICE_CLASSES | ADB_COMPOSITE_CLASSES
IOS_CLASSES = frozenset({"IosDeviceClient"})

FAMILIES: dict[str, frozenset[str]] = {"ssh": SSH_CLASSES, "adb": ADB_CLASSES, "ios": IOS_CLASSES}
# Children a composite is searched for, in this order.
CHILD_ORDER = ("adb", "ios", "ssh")


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
    """The session and the driver at ``path`` a keyword talks to, failing cleanly if absent.

    Call with ``sessions.lock()`` held and inside ``mapped_errors``.
    """
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


def family_of(node: Any) -> tuple[str, str | None] | None:
    """The device family of ``node`` and the child that provides it, if any.

    Returns ``(family, child)``: ``child`` is None when ``node`` itself is
    the device driver, or the child name (``adb``, ``ios``, ``ssh``) of a
    composite. Returns None when nothing matches.
    """
    for family, names in FAMILIES.items():
        if is_a(node, names):
            return family, None
    children = getattr(node, "children", {}) or {}
    for name in CHILD_ORDER:
        child = children.get(name)
        if child is not None and is_a(child, FAMILIES[name]):
            return name, name
    return None


def child_path(path: str, child: str | None) -> str:
    if child is None:
        return path
    return f"{path}.{child}" if path else child


def connect_to_device(path: str, alias: str | None = None, session: str | None = None, **options: Any) -> Any:
    """Open the connection that fits the driver at ``path``; returns the alias or index."""
    from . import adb, ios, ssh

    with sessions.lock(), mapped_errors("Connect To Device"):
        _, node = resolve_node(path, session)
        found = family_of(node)
        if found is None:
            raise Failure(
                f"driver '{path}' is {describe(node)}, which is not an SSH, ADB or iOS driver "
                f"and has no {', '.join(CHILD_ORDER)} child that is one"
            )
        family, child = found
        target = child_path(path, child)
        if family == "ssh":
            index = ssh.open_ssh_connection(target, alias=alias, session=session, **options)
        elif family == "adb":
            index = adb.open_adb_device(target, alias=alias, session=session, **options)
        else:
            index = ios.open_ios_device(target, alias=alias, session=session, **options)
    return alias if alias else index


class ConnectKeywords:
    """Connect to a device over whichever channel its driver offers."""

    @keyword("Connect To Device")
    def connect_to_device(self, path: str, alias: str | None = None, session: str | None = None, **options: Any) -> Any:
        """Connect to the device at ``path`` over SSH, ADB or iOS, whichever its driver offers.

        The driver's client class decides:

        | =Driver= | =Opens with= |
        | ``SSHWrapper`` (also with ssh-mitm) | `Open SSH Connection` (needs ``Library SSHLibrary``) |
        | ``AdbServer``, ``AdbDevice``, androidemulator | `Open ADB Device` |
        | ``IosDevice``, iossimulator's ``ios`` | `Open iOS Device` |

        A composite that is none of these is searched for an ``adb``,
        ``ios`` or ``ssh`` child, in that order, so ``Connect To Device
        simulator`` opens ``simulator.ios``. ``options`` are passed to the
        keyword that opens the connection, for example ``password=`` for
        SSH, ``serial=`` and ``timeout=`` for ADB or ``protocol=`` for iOS.

        Returns ``alias`` when given, else the connection's index.

        | Connect To Device | dut.ssh | alias=dut |
        | Execute Command | uname -a | # SSHLibrary, over the tunnel |
        | Connect To Device | android | timeout=5 min |
        | ADB Shell | getprop ro.product.model |
        | Connect To Device | simulator |
        | Run iOS Tool | idb | describe |
        """
        return connect_to_device(path, alias=alias, session=session, **options)
