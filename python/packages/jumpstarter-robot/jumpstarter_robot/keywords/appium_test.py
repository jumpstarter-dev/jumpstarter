"""Tests for the Appium bridge keywords.

The exporter side is the real ``AppiumDriver`` running the Appium package's
fake ``appium`` launcher, so ``serve()``, the HTTPS listener, its CA and
uploads are all real. WebDriver sessions are either real (over verified TLS to
the fake) or a recording stand-in for ``appium.webdriver.Remote``.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import appium.webdriver
import pytest
import robot
from AppiumLibrary import AppiumLibrary
from jumpstarter_driver_appium.common import APP_TOKEN_PREFIX, AppiumTarget
from jumpstarter_driver_appium.driver import AppiumDriver
from jumpstarter_driver_appium.testing import write_fake_appium
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from robot.api import ExecutionResult, Failure
from robot.libraries.BuiltIn import BuiltIn

from .. import context, sessions
from ..driver_library import Driver as DriverLibrary
from ..testing import use_client
from . import appium as bridge
from .appium import INSTALL_HINT, AppiumKeywords
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver

FIXED = {"platformName": "Android", "appium:uuid": "emu-1"}


# ---- a Device Hub stand-in: an iOS device client whose Appium runs on this machine ----

HUB_CALLS: list[tuple] = []


@dataclass(kw_only=True)
class FakeHub(Driver):
    @classmethod
    def client(cls) -> str:
        return f"{__name__}.FakeHubClient"


class FakeHubClient(DriverClient):
    @contextmanager
    def devicehub_appium(self, xctestrun, *, appium="appium") -> Generator[AppiumTarget, None, None]:
        HUB_CALLS.append(("enter", str(xctestrun), appium))
        try:
            yield AppiumTarget(
                url="http://127.0.0.1:4723",
                default_capabilities={"platformName": "iOS", "appium:uuid": "IPHONE-1"},
                wda_url="http://127.0.0.1:8100",
            )
        finally:
            HUB_CALLS.append(("exit",))


class FakeRemote:
    """Records how ``appium.webdriver.Remote`` was called instead of connecting."""

    created: ClassVar[list[FakeRemote]] = []

    def __init__(self, command_executor, options, client_config):
        self.command_executor = command_executor
        self.options = options
        self.client_config = client_config
        self.session_id = f"fake-{len(FakeRemote.created) + 1}"
        self.quits = 0
        FakeRemote.created.append(self)

    @property
    def capabilities(self):
        return self.options.to_capabilities()

    def quit(self):
        self.quits += 1


@pytest.fixture
def fake_appium(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(tmp_path / "record.jsonl"))
    return write_fake_appium(tmp_path)


def _appium_driver(executable: Path, state: Path) -> AppiumDriver:
    state.mkdir()
    return AppiumDriver(executable=str(executable), home=str(state), capabilities=FIXED, state_dir=str(state))


@pytest.fixture
def client(fake_appium, tmp_path):
    HUB_CALLS.clear()
    root = Composite(
        children={
            "appium": _appium_driver(fake_appium, tmp_path / "phone"),
            "tablet": _appium_driver(fake_appium, tmp_path / "tablet"),
            "power": MockPower(),
            "hub": FakeHub(),
        }
    )
    with serve(root) as served, use_client(served):
        try:
            yield served
        finally:
            context.end_run()
            leaked = bridge.servers()
            for server in leaked:  # never leave a listener behind: serve() would wait for it
                bridge._shutdown(server)
    assert leaked == []


@pytest.fixture
def keywords():
    return AppiumKeywords()


@pytest.fixture
def library(monkeypatch):
    """A real AppiumLibrary instance, returned as the suite's ``AppiumLibrary``."""
    instance = AppiumLibrary()
    asked: list[str | None] = []

    def get_library_instance(self, name=None, all=False):
        asked.append(name)
        if name != "AppiumLibrary":
            raise RuntimeError(f"No library '{name}' found.")
        return instance

    monkeypatch.setattr(BuiltIn, "get_library_instance", get_library_instance)
    instance.asked = asked
    return instance


@pytest.fixture
def remote(monkeypatch):
    FakeRemote.created = []
    monkeypatch.setattr(appium.webdriver, "Remote", FakeRemote)
    return FakeRemote


def _apk(directory: Path, name: str = "demo.apk") -> Path:
    path = directory / name
    path.write_bytes(b"PK\x03\x04 not really an apk")
    return path


# ---- serving ----


def test_start_serves_https_once_per_alias(client, keywords):
    url = keywords.start_appium_server("appium")
    assert url.startswith("https://127.0.0.1:")
    assert keywords.start_appium_server("appium") == url
    assert len(bridge.servers()) == 1

    target = keywords.get_appium_target()
    assert target.url == url and target.alias == "appium" and target.path == "appium"
    assert target.session == "default" and target.wda_url is None
    assert Path(target.ca_file).is_file()
    assert target.capabilities["appium:uuid"] == "emu-1"
    assert keywords.get_appium_target("appium").url == url


def test_stop_removes_the_listener_and_ca_file(client, keywords):
    keywords.start_appium_server("appium")
    ca_file = Path(keywords.get_appium_target().ca_file)
    keywords.stop_appium_server()
    assert not ca_file.exists()
    assert bridge.servers() == []
    keywords.stop_appium_server()  # nothing running: a no-op
    with pytest.raises(Failure, match="no Appium server 'appium' is running"):
        keywords.stop_appium_server("appium")
    with pytest.raises(Failure, match="no Appium server is running"):
        keywords.get_appium_target()


def test_restart_after_stop_gives_a_fresh_listener(client, keywords):
    first = keywords.start_appium_server("appium")
    keywords.stop_appium_server("appium")
    second = keywords.start_appium_server("appium")
    assert second.startswith("https://127.0.0.1:")
    assert len(bridge.servers()) == 1 and bridge.servers()[0].target.url in (first, second)


def test_fixed_port(client, keywords):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert keywords.start_appium_server("appium", port=port) == f"https://127.0.0.1:{port}"
    with pytest.raises(Failure, match="port must be an integer"):
        keywords.start_appium_server("tablet", port="80")


def test_servers_end_with_their_scope(client, keywords):
    keywords.start_appium_server("appium", scope="suite")
    keywords.start_appium_server("tablet", scope="global")
    context.end_suite("s1")  # started outside a suite, so any suite end closes it
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.start_appium_server("appium", scope="forever")
    assert [s.alias for s in bridge.servers()] == ["tablet"]


def test_servers_end_with_their_session(client, keywords):
    keywords.start_appium_server("appium")
    ca_file = Path(keywords.get_appium_target().ca_file)
    sessions.close("default")
    assert bridge.servers() == []
    assert not ca_file.exists()


def test_start_needs_an_appium_driver(client, keywords):
    with pytest.raises(Failure, match=r"'power' .* is PowerClient, not an Appium driver \(it has no serve\(\)\)"):
        keywords.start_appium_server("power")
    with pytest.raises(Failure, match="'nope'.* is not present"):
        keywords.start_appium_server("nope")
    assert bridge.servers() == []


def test_aliases_are_unique(client, keywords):
    keywords.start_appium_server("appium", alias="phone")
    with pytest.raises(Failure, match="alias 'phone' is already running for driver 'appium'"):
        keywords.start_appium_server("tablet", alias="phone")
    keywords.start_appium_server("tablet")
    assert sorted(s.alias for s in bridge.servers()) == ["phone", "tablet"]
    # with no alias, the server used last
    assert keywords.get_appium_target().alias == "tablet"


def test_default_alias_avoids_another_sessions_server(client, keywords, fake_appium, tmp_path):
    keywords.start_appium_server("appium")
    other = Composite(children={"appium": _appium_driver(fake_appium, tmp_path / "other")})
    with serve(other) as served, use_client(served, "ios"):
        keywords.start_appium_server("appium", session="ios")
        assert sorted(s.alias for s in bridge.servers()) == ["appium", "appium@ios"]
        by_alias, by_path = bridge._find("appium", "ios"), bridge._find("appium", "ios", by_alias=False)
        # the default session's alias does not stand in for the session asked for
        assert by_alias is not None and by_alias.alias == "appium@ios"
        assert by_path is not None and by_path.alias == "appium@ios"
        found = bridge._find("appium", None)
        assert found is not None and found.alias == "appium"  # without a session, an alias wins over a path
        found = bridge._find("appium@ios", "ios")
        assert found is not None and found.alias == "appium@ios"
    assert [s.alias for s in bridge.servers()] == ["appium"]


def test_open_application_uses_the_session_asked_for(client, keywords, library, remote, fake_appium, tmp_path):
    default_url = keywords.start_appium_server("appium")
    other = Composite(children={"appium": _appium_driver(fake_appium, tmp_path / "other")})
    with serve(other) as served, use_client(served, "ios"):
        # not started yet for ios: the default session's server must not be used instead
        keywords.open_appium_application("appium", session="ios")
        ios = next(s for s in bridge.servers() if s.session == "ios")
        assert ios.alias == "appium@ios" and ios.target.url != default_url
        keywords.open_appium_application("appium", session="ios")
        keywords.open_appium_application("appium")
        assert [driver.command_executor for driver in remote.created] == [ios.target.url, ios.target.url, default_url]


# ---- uploads ----


def test_upload_app_returns_a_token(client, keywords, tmp_path, monkeypatch):
    token = keywords.upload_app("appium", str(_apk(tmp_path)))
    assert token.startswith(APP_TOKEN_PREFIX) and token.endswith("/demo.apk")

    bundle = tmp_path / "Demo.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_text("<plist/>")
    assert keywords.upload_app("appium", str(bundle)).endswith("/Demo.app.zip")

    monkeypatch.chdir(tmp_path)
    assert keywords.upload_app("appium", "demo.apk").endswith("/demo.apk")


def test_upload_app_failures(client, keywords, tmp_path):
    with pytest.raises(Failure, match="application '.*missing.apk' does not exist"):
        keywords.upload_app("appium", str(tmp_path / "missing.apk"))
    with pytest.raises(Failure, match="has no upload_app"):
        keywords.upload_app("power", str(_apk(tmp_path)))
    with pytest.raises(ValueError, match="plain names ending in"):
        keywords.upload_app("appium", str(_apk(tmp_path, "script.sh")))


# ---- opening applications ----


def test_open_application_verifies_the_lease_ca(client, keywords, library, remote):
    url = keywords.start_appium_server("appium")
    ca_file = keywords.get_appium_target().ca_file
    index = keywords.open_appium_application(
        "appium", alias="settings", **{"appium:appPackage": "com.android.settings"}
    )
    assert index == 1
    (driver,) = remote.created
    assert driver.command_executor == url
    config = driver.client_config
    assert config.remote_server_addr == url
    assert config.ca_certs == ca_file
    assert config.ignore_certificates is False
    assert config.direct_connection is False
    assert config.keep_alive is True
    capabilities = driver.options.to_capabilities()
    assert capabilities["appium:uuid"] == "emu-1" and capabilities["platformName"].lower() == "android"
    assert capabilities["appium:appPackage"] == "com.android.settings"
    assert library._cache.current is driver
    assert library._cache.get_connection("settings") is driver
    assert library.asked == ["AppiumLibrary"]


def test_capabilities_override_target_defaults(client, keywords, library, remote):
    keywords.open_appium_application("appium", **{"appium:uuid": "other", "appium:noReset": True})
    capabilities = remote.created[0].options.to_capabilities()
    assert capabilities["appium:uuid"] == "other"  # the exporter's policy pins it back
    assert capabilities["appium:noReset"] is True


def test_open_application_uploads_local_apps(client, keywords, library, remote, tmp_path):
    keywords.open_appium_application("appium", app=str(_apk(tmp_path)))
    uploaded = remote.created[0].options.to_capabilities()["appium:app"]
    assert uploaded.startswith(APP_TOKEN_PREFIX) and uploaded.endswith("/demo.apk")

    keywords.open_appium_application("appium", app=uploaded)
    assert remote.created[1].options.to_capabilities()["appium:app"] == uploaded

    with pytest.raises(Failure, match="does not exist on this machine"):
        keywords.open_appium_application("appium", app=str(tmp_path / "gone.apk"))
    assert len(remote.created) == 2


def test_open_application_starts_the_server_for_a_path(client, keywords, library, remote):
    assert bridge.servers() == []
    keywords.open_appium_application("tablet")
    (server,) = bridge.servers()
    assert server.alias == "tablet" and server.path == "tablet"
    assert remote.created[0].command_executor == server.target.url


def test_open_application_by_server_alias(client, keywords, library, remote):
    keywords.start_appium_server("appium")
    url = keywords.start_appium_server("tablet", alias="big")
    keywords.open_appium_application("big")
    assert remote.created[0].command_executor == url
    assert len(bridge.servers()) == 2


def test_stop_closes_only_its_own_applications(client, keywords, library, remote):
    keywords.start_appium_server("appium")
    keywords.start_appium_server("tablet")
    assert keywords.open_appium_application("tablet", alias="t") == 1
    assert keywords.open_appium_application("appium", alias="a1") == 2
    assert keywords.open_appium_application("appium", alias="a2") == 3
    tablet, first, second = remote.created
    library.switch_application("t")

    library.switch_application("a2")
    library.close_application()  # closed by the test itself: not closed twice
    library.switch_application("t")

    keywords.stop_appium_server("appium")
    assert (first.quits, second.quits, tablet.quits) == (1, 1, 0)
    assert library._cache.current is tablet  # the current application stays current
    assert [s.alias for s in bridge.servers()] == ["tablet"]

    keywords.stop_appium_server()
    assert tablet.quits == 1
    assert library._cache.current_index is None


def test_stop_survives_applications_that_fail_to_close(client, keywords, library, remote):
    keywords.open_appium_application("appium")
    keywords.open_appium_application("tablet")
    broken = remote.created[0]
    broken.quit = lambda: (_ for _ in ()).throw(RuntimeError("session gone"))
    keywords.stop_appium_server("appium")
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    assert library._cache.current is remote.created[1]


def test_open_application_needs_appium_library(client, keywords, remote, monkeypatch):
    def not_imported(self, name=None, all=False):
        raise RuntimeError(f"No library '{name}' found.")

    monkeypatch.setattr(BuiltIn, "get_library_instance", not_imported)
    with pytest.raises(Failure, match="AppiumLibrary is not imported: add `Library    AppiumLibrary`"):
        keywords.open_appium_application("appium")
    assert bridge.servers() == [] and remote.created == []


def test_open_application_outside_a_run_says_appium_library_is_needed(client, keywords, remote):
    with pytest.raises(Failure, match="AppiumLibrary is not imported"):
        keywords.open_appium_application("appium")


def test_missing_appium_package_gives_the_install_hint(client, keywords, library, monkeypatch):
    monkeypatch.setitem(sys.modules, "appium", None)
    with pytest.raises(
        Failure, match=r"Appium-Python-Client is not installed .*pip install jumpstarter-robot\[appium\]"
    ):
        keywords.open_appium_application("appium")
    assert INSTALL_HINT == "pip install jumpstarter-robot[appium]"


def test_missing_appium_library_package_gives_the_install_hint(client, keywords, monkeypatch):
    monkeypatch.setitem(sys.modules, "AppiumLibrary", None)
    with pytest.raises(Failure, match=r"AppiumLibrary is not installed; pip install jumpstarter-robot\[appium\]"):
        keywords.open_appium_application("appium")


def test_fallback_without_the_application_cache(client, keywords, monkeypatch):
    """An AppiumLibrary without ``_cache`` opens the session itself, still verified."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/previous.pem")
    calls = []

    class OtherAppiumLibrary:
        def open_application(self, remote_url, alias=None, **kwargs):
            calls.append(("open", remote_url, alias, kwargs, os.environ.get("REQUESTS_CA_BUNDLE")))
            return 7

        def switch_application(self, index):
            calls.append(("switch", index))

        def close_application(self):
            calls.append(("close",))

    other = OtherAppiumLibrary()
    monkeypatch.setattr(BuiltIn, "get_library_instance", lambda self, name=None, all=False: other)
    url = keywords.start_appium_server("appium")
    ca_file = keywords.get_appium_target().ca_file
    assert keywords.open_appium_application("appium", alias="x", **{"appium:appPackage": "p"}) == 7
    (_, remote_url, alias, kwargs, bundle) = calls[0]
    assert remote_url == url and alias == "x" and bundle == ca_file
    assert kwargs["ignore_certificates"] is False and kwargs["direct_connection"] is False
    assert kwargs["appium:uuid"] == "emu-1" and kwargs["appium:appPackage"] == "p"
    assert os.environ["REQUESTS_CA_BUNDLE"] == "/etc/previous.pem"

    keywords.stop_appium_server()
    assert calls[1:] == [("switch", 7), ("close",)]


def test_refuses_a_client_config_that_ignores_certificates():
    target = SimpleNamespace(client_config=lambda **kwargs: SimpleNamespace(ignore_certificates=True))
    with pytest.raises(bridge.JumpstarterRobotError, match="ignores certificates"):
        bridge._verified_config(target)


def test_trusting_without_a_ca_leaves_the_environment(monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    with bridge._trusting(Path("/tmp/ca.pem")):
        assert os.environ["REQUESTS_CA_BUNDLE"] == "/tmp/ca.pem"
    assert "REQUESTS_CA_BUNDLE" not in os.environ
    with bridge._trusting(None):
        assert "REQUESTS_CA_BUNDLE" not in os.environ


# ---- Device Hub (Appium on this machine) ----


def test_device_hub_appium_runs_locally(client, keywords, library, remote, tmp_path):
    xctestrun = tmp_path / "WDA.xctestrun"
    url = keywords.start_device_hub_appium("hub", str(xctestrun), appium="/opt/appium")
    assert url == "http://127.0.0.1:4723"
    assert HUB_CALLS == [("enter", str(xctestrun), "/opt/appium")]
    target = keywords.get_appium_target("hub")
    assert target.ca_file is None and target.wda_url == "http://127.0.0.1:8100"

    app = _apk(tmp_path, "Demo.ipa")
    keywords.open_appium_application("hub", app=str(app), **{"appium:bundleId": "com.example"})
    (driver,) = remote.created
    capabilities = driver.options.to_capabilities()
    assert capabilities["appium:app"] == str(app.absolute())  # local Appium reads it directly
    assert capabilities["appium:uuid"] == "IPHONE-1"
    assert driver.command_executor == url and driver.client_config.direct_connection is False
    assert driver.client_config.ignore_certificates is False
    keywords.open_appium_application("hub", app="com.apple.Preferences")
    assert remote.created[1].options.to_capabilities()["appium:app"] == "com.apple.Preferences"

    keywords.stop_appium_server("hub")
    assert HUB_CALLS[-1] == ("exit",)
    assert driver.quits == 1


def test_device_hub_needs_an_ios_device(client, keywords, tmp_path):
    with pytest.raises(Failure, match="not an iOS device driver with Device Hub"):
        keywords.start_device_hub_appium("appium", str(tmp_path / "WDA.xctestrun"))


# ---- the node extension ----


def test_appium_nodes_get_an_open_application_shortcut(client, library, remote):
    appium_library = DriverLibrary("appium", client="jumpstarter_driver_appium.client.AppiumClient")
    (name,) = [n for n in appium_library.get_keyword_names() if n.endswith("Open Application")]
    assert name == "Appium Open Application"
    assert "alias" in str(appium_library.get_keyword_arguments(name))
    power_library = DriverLibrary("power", client="jumpstarter_driver_power.client.PowerClient")
    assert not [n for n in power_library.get_keyword_names() if n.endswith("Open Application")]

    index = appium_library.run_keyword(name, [], {"alias": "short", "appium:appPackage": "p"})
    assert index == 1
    (server,) = bridge.servers()
    assert server.path == "appium"
    assert remote.created[0].options.to_capabilities()["appium:appPackage"] == "p"


# ---- real TLS and real AppiumLibrary ----


def test_real_session_over_verified_tls(client, keywords, library):
    keywords.start_appium_server("appium")
    keywords.open_appium_application("appium", **{"appium:appPackage": "com.android.settings"})
    driver = library._cache.current
    assert driver.session_id == "s1"  # created by the fake Appium behind the exporter
    assert "com.android.settings" in json.dumps(driver.caps)
    keywords.stop_appium_server()


def test_the_lease_ca_is_required(client, keywords):
    """Without the lease CA the same connection fails: verification is really on."""
    from appium.options.common import AppiumOptions
    from appium.webdriver.client_config import AppiumClientConfig

    url = keywords.start_appium_server("appium")
    untrusted = AppiumClientConfig(remote_server_addr=url, direct_connection=False, timeout=5)
    with pytest.raises(Exception, match="(?i)certificate"):
        appium.webdriver.Remote(command_executor=url, options=AppiumOptions(), client_config=untrusted)


SUITE = """
*** Settings ***
Library    OperatingSystem
Library    AppiumLibrary
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    appium    client=jumpstarter_driver_appium.client.AppiumClient    AS    appium

*** Test Cases ***
Open Applications Through Jumpstarter
    ${url}=    Start Appium Server    appium
    Should Start With    ${url}    https://127.0.0.1:
    ${target}=    Get Appium Target
    Should Be Equal    ${target.capabilities}[appium:uuid]    emu-1
    File Should Exist    ${target.ca_file}
    ${first}=    Open Appium Application    appium    alias=settings    appium:appPackage=com.android.settings
    Should Be Equal As Integers    ${first}    1
    ${second}=    Appium Open Application    alias=second    app=${APP}
    Should Be Equal As Integers    ${second}    2
    Switch Application    settings
    Stop Appium Server
    Run Keyword And Expect Error    *No application is open*    Get Source
    File Should Not Exist    ${target.ca_file}

Clear Failures
    Run Keyword And Expect Error    *no Appium server 'nope' is running*    Get Appium Target    nope
    Run Keyword And Expect Error    *is PowerClient, not an Appium driver*    Start Appium Server    power
    Run Keyword And Expect Error    *does not exist on this machine*
    ...    Open Appium Application    appium    app=/no/such.apk

Server Ends With The Run
    Start Appium Server    tablet    alias=kept
"""

NO_LIBRARY_SUITE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter

*** Test Cases ***
AppiumLibrary Must Be Imported
    Run Keyword And Expect Error    *AppiumLibrary is not imported*    Open Appium Application    appium
"""


def _run(tmp_path: Path, source: str, **variables):
    suite = tmp_path / "appium.robot"
    suite.write_text(source)
    output = tmp_path / "output.xml"
    rc = robot.run(
        str(suite),
        outputdir=str(tmp_path),
        output=str(output),
        report="NONE",
        log="NONE",
        console="none",
        variable=[f"{name}:{value}" for name, value in variables.items()],
    )
    result = ExecutionResult(str(output))
    statuses = {test.name: (test.status, test.message) for test in result.suite.all_tests}
    assert rc == 0, statuses
    return result


def test_robot_suite_with_appium_library(client, tmp_path):
    result = _run(tmp_path, SUITE, APP=_apk(tmp_path))
    assert {test.name: test.status for test in result.suite.all_tests} == {
        "Open Applications Through Jumpstarter": "PASS",
        "Clear Failures": "PASS",
        "Server Ends With The Run": "PASS",
    }
    assert bridge.servers() == []  # the Jumpstarter library closed it at the end of the run


def test_robot_suite_without_appium_library(client, tmp_path):
    _run(tmp_path, NO_LIBRARY_SUITE)
    assert bridge.servers() == []
