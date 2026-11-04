"""Appium: serve a leased target's Appium and open AppiumLibrary sessions on it.

`Start Appium Server` enters ``AppiumClient.serve()``, which exposes the
exporter's Appium on a loopback HTTPS port that trusts only the lease's
certificate authority. `Open Appium Application` then creates the WebDriver
session itself and hands it to AppiumLibrary, so every AppiumLibrary keyword
(``Click Element``, ``Capture Page Screenshot``...) works on it.

The session is created here rather than with AppiumLibrary's own
``Open Application`` because that keyword defaults to
``ignore_certificates=True`` and ``direct_connection=True`` and has no way to
name a CA file. Here certificate and hostname verification stay on, and direct
connections (which would bypass the lease tunnel) stay off.

Servers are shared by every library instance in the run and closed at the end
of their scope, before their session's connection goes away, or by
`Stop Appium Server`, which first closes the applications opened through them.
Appium, Selenium and AppiumLibrary are imported only when a keyword runs.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable, Generator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import DotDict

from .. import context, sessions
from ..errors import JumpstarterRobotError, mapped_errors
from ..extensions import register_node_extension

logger = logging.getLogger(__name__)

INSTALL_HINT = "pip install jumpstarter-robot[appium]"
LIBRARY_NAME = "AppiumLibrary"
APP_TOKEN_PREFIX = "jumpstarter-app:"  # jumpstarter_driver_appium.common.APP_TOKEN_PREFIX
DEFAULT_PATH = "appium"


@dataclass(eq=False)
class AppiumServer:
    """One served Appium target and the AppiumLibrary applications opened on it."""

    alias: str
    session: str
    path: str
    target: Any  # jumpstarter_driver_appium.common.AppiumTarget
    node: Any
    uploads: bool  # whether local application files are uploaded to the exporter first
    stack: ExitStack
    # (AppiumLibrary instance, WebDriver) or, without its cache, (instance, application index)
    applications: list[tuple[Any, Any]] = field(default_factory=list)
    closer: Callable[[], None] | None = None


_servers: dict[str, AppiumServer] = {}
_current: list[str] = []  # aliases, most recently started or used last


def servers() -> list[AppiumServer]:
    """The running servers, oldest first."""
    with sessions.lock():
        return list(_servers.values())


# ---- optional dependencies ----


def _require_appium() -> tuple[Any, Any]:
    """``appium.webdriver`` and ``AppiumOptions``, or a failure with the install hint."""
    try:
        from appium import webdriver
        from appium.options.common import AppiumOptions
    except ImportError as e:
        raise JumpstarterRobotError(f"Appium-Python-Client is not installed ({e}); {INSTALL_HINT}") from None
    return webdriver, AppiumOptions


def _appium_library() -> Any:
    """The suite's AppiumLibrary instance, or a failure saying how to import it."""
    from robot.libraries.BuiltIn import BuiltIn

    try:
        return BuiltIn().get_library_instance(LIBRARY_NAME)
    except (RuntimeError, AttributeError):  # not imported / RobotNotRunningError
        try:
            import AppiumLibrary  # noqa: F401
        except ImportError:
            raise JumpstarterRobotError(f"AppiumLibrary is not installed; {INSTALL_HINT}") from None
        raise JumpstarterRobotError(
            "AppiumLibrary is not imported: add `Library    AppiumLibrary` to the suite settings "
            "so its keywords can drive the application"
        ) from None


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
            raise JumpstarterRobotError("no Appium server is running; use `Start Appium Server` first")
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
    uploads: bool,
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
    server = AppiumServer(alias, opened.name, path, target, node, uploads, stack)
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
    """Close the applications opened through ``server``, then the server itself."""
    with sessions.lock():
        if _servers.get(server.alias) is server:
            del _servers[server.alias]
        if server.alias in _current and server.alias not in _servers:
            _current.remove(server.alias)  # a newer server may have taken the alias
        applications, server.applications = server.applications, []
    try:
        _close_applications(applications)
    finally:
        server.stack.close()


# ---- closing AppiumLibrary applications ----


def _is_open(cache: Any, driver: Any) -> bool:
    return driver in getattr(cache, "_connections", ()) and driver not in getattr(cache, "_closed", ())


def _close_applications(applications: list[tuple[Any, Any]]) -> None:
    """Close each application in its AppiumLibrary, keeping that library's other current application."""
    by_library: dict[int, tuple[Any, list[Any]]] = {}
    for library, application in applications:
        by_library.setdefault(id(library), (library, []))[1].append(application)
    for library, owned in by_library.values():
        cache = getattr(library, "_cache", None)
        if cache is not None and callable(getattr(cache, "switch", None)):
            _close_in_cache(library, cache, owned)
        else:
            _close_by_index(library, owned)


def _close_in_cache(library: Any, cache: Any, drivers: list[Any]) -> None:
    previous = cache.current
    for driver in drivers:
        if not _is_open(cache, driver):
            continue
        try:
            cache.switch(driver)
            library.close_application()
        except Exception:  # a dead session must not keep the server open
            logger.warning("could not close an Appium application", exc_info=True)
            with contextlib.suppress(Exception):
                cache._closed.add(driver)
                cache.current_index = None
    if previous not in drivers and _is_open(cache, previous):
        cache.switch(previous)


def _close_by_index(library: Any, indexes: list[Any]) -> None:
    for index in indexes:
        try:
            library.switch_application(index)
            library.close_application()
        except Exception:  # the index may already be closed or reused
            logger.warning("could not close Appium application %s", index, exc_info=True)


# ---- opening applications ----


def _application(server: AppiumServer, app: str | os.PathLike | None) -> str | None:
    """``app`` as Appium on the server can use it: local files on remote targets are uploaded."""
    if app is None or str(app) == "":
        return None
    value = str(app)
    if value.startswith(APP_TOKEN_PREFIX) or "://" in value:
        return value
    local = Path(value).expanduser()
    if not local.exists():
        if server.uploads:
            raise JumpstarterRobotError(
                f"application '{value}' does not exist on this machine; give a local file, an .app bundle "
                f"or a token from `Upload App`"
            )
        return value  # local Appium: a path or name only it understands
    if not server.uploads:
        return str(local.absolute())
    token = server.node.upload_app(local.absolute())
    robot_logger.info(f"Uploaded {local.name} as {token}")
    return token


@contextlib.contextmanager
def _trusting(ca_file: Path | None) -> Generator[None, None, None]:
    """Make Selenium's default trust the target's CA while a client config is built."""
    if ca_file is None:
        yield
        return
    saved = os.environ.get("REQUESTS_CA_BUNDLE")
    os.environ["REQUESTS_CA_BUNDLE"] = str(ca_file)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
        else:
            os.environ["REQUESTS_CA_BUNDLE"] = saved


def _verified_config(target: Any) -> Any:
    config = target.client_config(direct_connection=False, keep_alive=True)
    if getattr(config, "ignore_certificates", False):  # never expected; refuse rather than connect unverified
        raise JumpstarterRobotError("refusing an Appium client configuration that ignores certificates")
    return config


def _open_application(
    server_or_path: str | None,
    alias: str | None,
    app: str | os.PathLike | None,
    session: str | None,
    capabilities: dict[str, Any],
    *,
    by_alias: bool = True,
) -> int:
    webdriver, appium_options = _require_appium()
    library = _appium_library()
    server = _find(server_or_path, session, by_alias=by_alias)
    if server is None:
        if server_or_path is None:
            raise JumpstarterRobotError("give an Appium server alias or driver path, or use `Start Appium Server`")
        server = _start_appium(server_or_path, None, 0, "global", session)
    _touch(server)
    capabilities = server.target.capabilities() | dict(capabilities)
    token = _application(server, app)
    if token is not None:
        capabilities["appium:app"] = token
    cache = getattr(library, "_cache", None)
    if cache is None or not callable(getattr(cache, "register", None)):
        # Not the AppiumLibrary this was written for: let it open the session, still verified.
        with _trusting(server.target.ca_file):
            index = library.open_application(
                server.target.url,
                alias=alias,
                ignore_certificates=False,
                direct_connection=False,
                keep_alive=True,
                **capabilities,
            )
        server.applications.append((library, index))
        return index
    options = appium_options().load_capabilities(capabilities)
    driver = webdriver.Remote(
        command_executor=server.target.url, options=options, client_config=_verified_config(server.target)
    )
    index = cache.register(driver, alias)
    server.applications.append((library, driver))
    robot_logger.info(f"Opened Appium session {driver.session_id} on '{server.alias}' as application {index}")
    return index


def _start_appium(path: str, alias: str | None, port: int, scope: str, session: str | None) -> AppiumServer:
    if isinstance(port, bool) or not isinstance(port, int):
        raise JumpstarterRobotError(f"port must be an integer, not {port!r}")
    return _start(
        path,
        alias,
        scope,
        session,
        method="serve",
        what="an Appium driver",
        enter=lambda node: node.serve(port=port),
        uploads=True,
    )


class AppiumKeywords:
    """Keywords that bridge Jumpstarter's Appium drivers to AppiumLibrary."""

    @keyword(tags=["appium"])
    def start_appium_server(
        self,
        path: str = DEFAULT_PATH,
        port: int = 0,
        alias: str | None = None,
        scope: str = "global",
        session: str | None = None,
    ) -> str:
        """Serve the Appium of the driver at ``path`` on a loopback HTTPS port; returns its URL.

        The exporter starts Appium on first use. ``port=0`` picks an unused
        local port. ``alias`` (default: the driver path) names the server for
        `Open Appium Application`, `Get Appium Target` and `Stop Appium Server`;
        starting it again while it runs returns the same URL. The server stops
        at the end of ``scope`` (``test``, ``suite`` or ``global``), when its
        session closes, or with `Stop Appium Server`.

        | ${url}= | Start Appium Server | appium |
        | Start Appium Server | emulator.appium | alias=android | scope=suite |
        """
        with sessions.lock(), mapped_errors("Start Appium Server"):
            return _start_appium(path, alias, port, scope, session).target.url

    @keyword(tags=["appium"])
    def start_device_hub_appium(
        self,
        path: str,
        xctestrun: str,
        appium: str = "appium",
        alias: str | None = None,
        scope: str = "global",
        session: str | None = None,
    ) -> str:
        """Show an iOS device in Device Hub, run WebDriverAgent and Appium on this Mac; returns Appium's URL.

        ``path`` is an iOS device driver, ``xctestrun`` the ``.xctestrun`` file
        of a WebDriverAgent build signed for the device, and ``appium`` your
        installed Appium launcher with the XCUITest driver. Appium runs here, so
        `Open Appium Application` passes local ``app=`` paths to it unchanged.
        Everything stops, and the device leaves Device Hub, with
        `Stop Appium Server` or at the end of ``scope``. macOS only.

        | Start Device Hub Appium | iphone | ${CURDIR}/WebDriverAgentRunner.xctestrun |
        | Open Appium Application | iphone | appium:bundleId=com.apple.Preferences |
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
                uploads=False,
            )
            return server.target.url

    @keyword(tags=["appium"])
    def get_appium_target(self, alias: str | None = None) -> DotDict:
        """Describe a running Appium server: ``url``, ``ca_file``, ``capabilities`` and ``wda_url``.

        Without ``alias``, the server started or used last. ``ca_file`` is the
        lease's CA certificate (empty for plain-HTTP local Appium), for other
        WebDriver clients; ``capabilities`` are the ones the target fixes.

        | ${target}= | Get Appium Target |
        | Log | ${target.url} |
        | ${caps}= | Set Variable | ${target.capabilities} |
        """
        with sessions.lock(), mapped_errors("Get Appium Target"):
            server = _require(alias)
            target = server.target
            return DotDict(
                url=target.url,
                ca_file=str(target.ca_file) if target.ca_file is not None else None,
                capabilities=DotDict(target.capabilities()),
                wda_url=target.wda_url,
                alias=server.alias,
                path=server.path,
                session=server.session,
            )

    @keyword(tags=["appium"])
    def upload_app(self, path: str, file: str, session: str | None = None) -> str:
        """Upload an application to the Appium driver at ``path``; returns its ``jumpstarter-app:`` token.

        ``file`` is an ``.apk``, ``.aab``, ``.apks``, ``.ipa`` or ``.zip`` file,
        or an ``.app`` bundle directory, which is zipped first. Use the token as
        the ``app`` of `Open Appium Application` or ``appium:app``. Uploads last
        until the target is reset or the lease ends.

        | ${token}= | Upload App | appium | ${CURDIR}/apps/ApiDemos-debug.apk |
        | Open Appium Application | appium | app=${token} |
        """
        with sessions.lock(), mapped_errors("Upload App"):
            opened = context.session(session)
            opened.check_alive()
            node = _node(opened, path, "upload_app", "an Appium driver")
            local = Path(file).expanduser()
            if not local.exists():
                raise JumpstarterRobotError(f"application '{file}' does not exist")
            token = node.upload_app(local.absolute())
            robot_logger.info(f"Uploaded {local.name} as {token}")
            return token

    @keyword(tags=["appium"])
    def open_appium_application(
        self,
        server_or_path: str,
        alias: str | None = None,
        app: str | None = None,
        *,
        session: str | None = None,
        **capabilities: Any,
    ) -> int:
        """Open an AppiumLibrary application on a Jumpstarter Appium server; returns its index.

        ``server_or_path`` is a server alias or the path of an Appium driver,
        whose server is started (for the whole run) if it is not running.
        With ``session``, only that session's servers are considered.
        ``capabilities`` are added to the ones the target fixes. A local
        ``app`` file or ``.app`` bundle is uploaded first; ``jumpstarter-app:``
        tokens are used as they are. ``alias`` names the application for
        AppiumLibrary's ``Switch Application``.

        The session verifies the lease's certificate and never uses direct
        connections. AppiumLibrary must be imported by the suite; all its
        keywords then act on the new application.

        | Open Appium Application | appium | alias=settings | appium:appPackage=com.android.settings |
        | Open Appium Application | appium | app=${CURDIR}/apps/ApiDemos-debug.apk |
        | Open Appium Application | simulator.appium | appium:bundleId=com.apple.Preferences | appium:noReset=${True} |
        | Click Element | accessibility_id=Views |
        """
        with sessions.lock(), mapped_errors("Open Appium Application"):
            return _open_application(server_or_path, alias, app, session, capabilities)

    @keyword(tags=["appium"])
    def stop_appium_server(self, alias: str | None = None) -> None:
        """Close the applications opened through an Appium server, then stop serving it.

        Without ``alias``, the server started or used last; with none running
        this does nothing. Other AppiumLibrary applications stay open and the
        current one stays current. The exporter keeps Appium itself running
        for the next `Start Appium Server`; Device Hub Appium stops.

        | Stop Appium Server |
        | Stop Appium Server | android |
        """
        with sessions.lock(), mapped_errors("Stop Appium Server"):
            if alias is None and not _current:
                robot_logger.info("No Appium server is running")
                return
            server = _require(alias)
            if server.closer is not None:
                server.closer()
            else:
                _shutdown(server)


def _open_application_on_node(
    path: str, session: str | None, alias: str | None = None, app: str | None = None, **capabilities: Any
) -> int:
    """Open an AppiumLibrary application on this Appium driver; returns its index.

    Starts serving the driver's Appium if needed, uploads a local ``app``, and
    keeps certificate verification on. Same as `Open Appium Application` with
    this driver's path; AppiumLibrary must be imported.

    | Open Application | alias=settings | appium:appPackage=com.android.settings | appium:appActivity=.Settings |
    """
    with sessions.lock(), mapped_errors("Open Application"):
        return _open_application(path, alias, app, session, capabilities, by_alias=False)


register_node_extension({"AppiumClient"}, "Open Application", _open_application_on_node, tags=("appium",))
