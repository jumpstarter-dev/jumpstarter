"""Appium: serve a leased target's Appium for AppiumLibrary.

`Start Appium` enters ``AppiumClient.serve()``, which exposes the exporter's
Appium on a loopback HTTPS port, and returns that URL with the capabilities
the target fixes (its platform, automation name and device). AppiumLibrary's
own ``Open Application`` then opens the session on it:

| ${appium}= | Start Appium | emulator.appium |
| Open Application | ${appium.url} | &{appium.capabilities} | appium:appPackage=com.android.settings |

The server is reached through the lease's tunnel, so AppiumLibrary's default
``ignore_certificates=True`` only skips checking the lease's own certificate.
Servers are shared by every library instance in the run and stop at the end of
their scope, before their session's connection goes away, or with
`Stop Appium`; close the applications on them first (``Close All Applications``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import DotDict

from .. import context, sessions
from ..errors import JumpstarterRobotError, mapped_errors

DEFAULT_PATH = "appium"


@dataclass(eq=False)
class AppiumServer:
    """One served Appium target."""

    alias: str
    session: str
    path: str
    target: Any  # jumpstarter_driver_appium.common.AppiumTarget
    node: Any
    stack: ExitStack
    closer: Callable[[], None] | None = None


_servers: dict[str, AppiumServer] = {}
_current: list[str] = []  # aliases, most recently started or used last


def servers() -> list[AppiumServer]:
    """The running servers, oldest first."""
    with sessions.lock():
        return list(_servers.values())


# ---- the server registry ----


def _touch(server: AppiumServer) -> None:
    if server.alias in _current:
        _current.remove(server.alias)
    _current.append(server.alias)


def _find(name: str | None, session: str | None, *, by_alias: bool = True) -> AppiumServer | None:
    """The server called ``name``, else the one started from driver path ``name`` in the session.

    An explicit ``session`` limits the alias to that session's servers, so the
    default alias of another session's server never stands in for this one.
    """
    if name is None:
        return _servers[_current[-1]] if _current else None
    session_name = context.session_name(session)
    if by_alias and name in _servers and (session is None or _servers[name].session == session_name):
        return _servers[name]
    for server in _servers.values():
        if server.path == name and server.session == session_name:
            return server
    return None


def _require(name: str | None, session: str | None = None) -> AppiumServer:
    server = _find(name, session)
    if server is None:
        if name is None:
            raise JumpstarterRobotError("no Appium server is running; use `Start Appium` first")
        running = ", ".join(sorted(_servers)) or "none"
        raise JumpstarterRobotError(f"no Appium server '{name}' is running; running: {running}")
    return server


def _default_alias(path: str, session_name: str) -> str:
    alias = path or DEFAULT_PATH
    existing = _servers.get(alias)
    if existing is not None and (existing.path, existing.session) != (path, session_name):
        return f"{alias}@{session_name}"
    return alias


def _node(session: sessions.Session, path: str, method: str, what: str) -> Any:
    try:
        node = session.resolve(path)
    except KeyError as e:
        raise JumpstarterRobotError(e.args[0]) from None
    if not callable(getattr(node, method, None)):
        raise JumpstarterRobotError(
            f"driver '{path or '(root)'}' on exporter '{session.exporter}' is {type(node).__name__}, "
            f"not {what} (it has no {method}())"
        )
    return node


def _start(
    path: str,
    alias: str | None,
    scope: str,
    session: str | None,
    *,
    method: str,
    what: str,
    enter: Callable[[Any], contextlib.AbstractContextManager],
) -> AppiumServer:
    """Enter ``enter(node)`` on its own stack and register the target it yields."""
    if scope.lower() not in context.SCOPES:
        raise JumpstarterRobotError(f"scope must be one of {', '.join(context.SCOPES)}, not '{scope}'")
    opened = context.session(session)
    opened.check_alive()
    alias = alias or _default_alias(path, opened.name)
    existing = _servers.get(alias)
    if existing is not None:
        if existing.path == path and existing.session == opened.name:
            robot_logger.info(f"Appium server '{alias}' is already running at {existing.target.url}")
            _touch(existing)
            return existing
        raise JumpstarterRobotError(
            f"an Appium server with alias '{alias}' is already running for driver '{existing.path}' "
            f"of session '{existing.session}'; stop it first or choose another alias"
        )
    node = _node(opened, path, method, what)
    stack = ExitStack()
    try:
        target = stack.enter_context(enter(node))
    except BaseException:
        stack.close()
        raise
    server = AppiumServer(alias, opened.name, path, target, node, stack)
    try:
        server.closer = context.add_scoped(lambda: _shutdown(server), scope, session=opened)
    except BaseException:
        stack.close()
        raise
    _servers[alias] = server
    _touch(server)
    robot_logger.info(f"Appium server '{alias}' for '{path}' is at {target.url}")
    return server


def _shutdown(server: AppiumServer) -> None:
    """Stop serving ``server``."""
    with sessions.lock():
        if _servers.get(server.alias) is server:
            del _servers[server.alias]
        if server.alias in _current and server.alias not in _servers:
            _current.remove(server.alias)  # a newer server may have taken the alias
    server.stack.close()


def _start_appium(path: str, alias: str | None, port: int, scope: str, session: str | None) -> AppiumServer:
    if isinstance(port, bool) or not isinstance(port, int):
        raise JumpstarterRobotError(f"port must be an integer, not {port!r}")
    return _start(
        path, alias, scope, session, method="serve", what="an Appium driver", enter=lambda node: node.serve(port=port)
    )


def _described(server: AppiumServer) -> DotDict:
    target = server.target
    return DotDict(
        url=target.url,
        capabilities=DotDict(target.capabilities()),
        ca_file=str(target.ca_file) if target.ca_file is not None else None,
        wda_url=target.wda_url,
        alias=server.alias,
        path=server.path,
        session=server.session,
    )


class AppiumKeywords:
    """Keywords that serve Jumpstarter's Appium targets to AppiumLibrary."""

    @keyword(tags=["appium"])
    def start_appium(
        self,
        path: str = DEFAULT_PATH,
        port: int = 0,
        alias: str | None = None,
        scope: str = "global",
        session: str | None = None,
    ) -> DotDict:
        """Serve the Appium of the driver at ``path`` on a loopback HTTPS port.

        Returns ``url`` and ``capabilities`` (the ones the target fixes: its
        platform, automation name and device) for AppiumLibrary's
        ``Open Application``, plus ``ca_file`` (the lease's CA certificate, for
        other WebDriver clients), ``wda_url`` and ``alias``.

        The exporter starts Appium on first use. ``port=0`` picks an unused
        local port. ``alias`` (default: the driver path) names the server for
        `Stop Appium`; starting it again while it runs returns the same server.
        It stops at the end of ``scope`` (``test``, ``suite`` or ``global``),
        when its session closes, or with `Stop Appium`.

        | ${appium}= | Start Appium | emulator.appium |
        | Open Application | ${appium.url} | &{appium.capabilities} | appium:appPackage=com.android.settings |
        """
        with sessions.lock(), mapped_errors("Start Appium"):
            return _described(_start_appium(path, alias, port, scope, session))

    @keyword(tags=["appium"])
    def start_device_hub_appium(
        self,
        path: str,
        xctestrun: str,
        appium: str = "appium",
        alias: str | None = None,
        scope: str = "global",
        session: str | None = None,
    ) -> DotDict:
        """Show an iOS device in Device Hub, run WebDriverAgent and Appium on this Mac.

        ``path`` is an iOS device driver, ``xctestrun`` the ``.xctestrun`` file
        of a WebDriverAgent build signed for the device, and ``appium`` your
        installed Appium launcher with the XCUITest driver. Returns the same
        ``url`` and ``capabilities`` as `Start Appium`, and sessions take the
        same capabilities as on the exporter: give `Upload App` the same
        ``path`` for an ``appium:app`` token. Everything stops, and
        the device leaves Device Hub, with `Stop Appium` or at the end of
        ``scope``. macOS only.

        | ${appium}= | Start Device Hub Appium | iphone | ${CURDIR}/WebDriverAgentRunner.xctestrun |
        | Open Application | ${appium.url} | &{appium.capabilities} | appium:bundleId=com.apple.Preferences |
        """
        with sessions.lock(), mapped_errors("Start Device Hub Appium"):
            server = _start(
                path,
                alias,
                scope,
                session,
                method="devicehub_appium",
                what="an iOS device driver with Device Hub",
                enter=lambda node: node.devicehub_appium(xctestrun, appium=appium),
            )
            return _described(server)

    @keyword(tags=["appium"])
    def upload_app(self, path: str, file: str, session: str | None = None) -> str:
        """Upload an application for the Appium at ``path``; returns its ``jumpstarter-app:`` token.

        ``path`` is an Appium driver, or the alias or driver path of a running
        server, such as one from `Start Device Hub Appium`, which takes its
        applications this way. ``file`` is an ``.apk``, ``.aab``, ``.apks``,
        ``.ipa`` or ``.zip`` file, or an ``.app`` bundle directory. Give the
        token to ``Open Application`` as ``appium:app``. Uploads last until the
        target is reset or the lease ends; a token works only on the target
        that issued it.

        | ${token}= | Upload App | appium | ${CURDIR}/apps/ApiDemos-debug.apk |
        | Open Application | ${appium.url} | &{appium.capabilities} | appium:app=${token} |
        """
        with sessions.lock(), mapped_errors("Upload App"):
            opened = context.session(session)
            opened.check_alive()
            local = Path(file).expanduser()
            server = _find(path, session)
            if server is not None and getattr(server.target, "uploader", None) is not None:
                if not local.exists():
                    raise JumpstarterRobotError(f"application '{file}' does not exist")
                token = server.target.upload_app(local.absolute())
                robot_logger.info(f"Uploaded {local.name} as {token}")
                return token
            node = _node(opened, path, "upload_app", "an Appium driver")
            if not local.exists():
                raise JumpstarterRobotError(f"application '{file}' does not exist")
            token = node.upload_app(local.absolute())
            robot_logger.info(f"Uploaded {local.name} as {token}")
            return token

    @keyword(tags=["appium"])
    def stop_appium(self, alias: str | None = None) -> None:
        """Stop serving an Appium server; close its applications first (``Close All Applications``).

        Without ``alias``, the server started or used last; with none running
        this does nothing. The exporter keeps Appium itself running for the
        next `Start Appium`; Device Hub Appium stops.

        | Close All Applications |
        | Stop Appium |
        """
        with sessions.lock(), mapped_errors("Stop Appium"):
            if alias is None and not _current:
                robot_logger.info("No Appium server is running")
                return
            server = _require(alias)
            if server.closer is not None:
                server.closer()
            else:
                _shutdown(server)
