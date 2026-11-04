"""Tests for the Appium keywords.

The exporter side is the real ``AppiumDriver`` running the Appium package's
fake ``appium`` launcher, so ``serve()``, the HTTPS listener, its CA and
uploads are all real.
"""

from __future__ import annotations

import socket
import textwrap
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import robot
from jumpstarter_driver_appium.common import APP_TOKEN_PREFIX, AppiumTarget
from jumpstarter_driver_appium.driver import AppiumDriver
from jumpstarter_driver_appium.testing import write_fake_appium
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from robot.api import ExecutionResult, Failure

from .. import context, sessions
from ..testing import use_client
from . import appium as bridge
from .appium import AppiumKeywords
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver

FIXED = {"platformName": "Android", "appium:uuid": "emu-1"}


# ---- a Device Hub stand-in: an iOS device client whose Appium runs on this machine ----

HUB_CALLS: list[tuple] = []


def _hub_upload(path: Path) -> str:
    HUB_CALLS.append(("upload", path.name))
    return f"jumpstarter-app:hub/{path.name}"


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
                uploader=_hub_upload,
            )
        finally:
            HUB_CALLS.append(("exit",))


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


def _apk(directory: Path, name: str = "demo.apk") -> Path:
    path = directory / name
    path.write_bytes(b"PK\x03\x04 not really an apk")
    return path


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---- Start Appium ----


def test_start_returns_the_url_and_capabilities(client, keywords):
    appium = keywords.start_appium("appium")
    assert appium.url.startswith("https://127.0.0.1:")
    assert appium.capabilities == FIXED
    assert appium.alias == "appium" and appium.path == "appium" and appium.session == "default"
    assert appium.wda_url is None
    assert Path(appium.ca_file).is_file()
    # the DotDict reads like ${appium.url} and &{appium.capabilities} in Robot
    assert appium["url"] == appium.url and appium.capabilities["appium:uuid"] == "emu-1"


def test_starting_again_returns_the_running_server(client, keywords):
    first = keywords.start_appium("appium")
    second = keywords.start_appium("appium")
    assert second == first
    assert len(bridge.servers()) == 1


def test_capabilities_are_a_copy(client, keywords):
    keywords.start_appium("appium")["capabilities"]["appium:noReset"] = True
    assert keywords.start_appium("appium").capabilities == FIXED


def test_restart_after_stop_gives_a_fresh_listener(client, keywords):
    first = keywords.start_appium("appium")
    keywords.stop_appium("appium")
    assert not Path(first.ca_file).exists()
    second = keywords.start_appium("appium")
    assert second.url.startswith("https://127.0.0.1:") and Path(second.ca_file).is_file()
    assert len(bridge.servers()) == 1


def test_fixed_port(client, keywords):
    port = _unused_port()
    assert keywords.start_appium("appium", port=port).url == f"https://127.0.0.1:{port}"
    with pytest.raises(Failure, match="port must be an integer"):
        keywords.start_appium("tablet", port="80")
    with pytest.raises(Failure, match="port must be an integer"):
        keywords.start_appium("tablet", port=True)
    assert [s.alias for s in bridge.servers()] == ["appium"]


def test_start_needs_an_appium_driver(client, keywords):
    with pytest.raises(Failure, match=r"'power' .* is PowerClient, not an Appium driver \(it has no serve\(\)\)"):
        keywords.start_appium("power")
    with pytest.raises(Failure, match="'nope'.* is not present"):
        keywords.start_appium("nope")
    assert bridge.servers() == []


# ---- aliases ----


def test_aliases_are_unique(client, keywords):
    keywords.start_appium("appium", alias="phone")
    with pytest.raises(Failure, match="alias 'phone' is already running for driver 'appium'"):
        keywords.start_appium("tablet", alias="phone")
    assert keywords.start_appium("tablet").alias == "tablet"
    assert sorted(s.alias for s in bridge.servers()) == ["phone", "tablet"]
    assert keywords.start_appium("appium", alias="phone").alias == "phone"  # the same server again


def test_default_alias_avoids_another_sessions_server(client, keywords, fake_appium, tmp_path):
    default = keywords.start_appium("appium")
    other = Composite(children={"appium": _appium_driver(fake_appium, tmp_path / "other")})
    with serve(other) as served, use_client(served, "ios"):
        ios = keywords.start_appium("appium", session="ios")
        assert ios.alias == "appium@ios" and ios.session == "ios" and ios.url != default.url
        assert keywords.start_appium("appium", session="ios") == ios
        assert sorted(s.alias for s in bridge.servers()) == ["appium", "appium@ios"]
        by_alias, by_path = bridge._find("appium", "ios"), bridge._find("appium", "ios", by_alias=False)
        # the default session's alias does not stand in for the session asked for
        assert by_alias is not None and by_alias.alias == "appium@ios"
        assert by_path is not None and by_path.alias == "appium@ios"
        found = bridge._find("appium", None)
        assert found is not None and found.alias == "appium"  # without a session, an alias wins over a path
    assert [s.alias for s in bridge.servers()] == ["appium"]  # the ios server ended with its session


# ---- scopes and sessions ----


def test_servers_end_with_their_scope(client, keywords):
    keywords.start_appium("appium", scope="suite")
    keywords.start_appium("tablet", scope="global")
    context.end_suite("s1")  # started outside a suite, so any suite end closes it
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.start_appium("appium", scope="forever")
    assert [s.alias for s in bridge.servers()] == ["tablet"]


def test_servers_end_with_their_session(client, keywords):
    ca_file = Path(keywords.start_appium("appium").ca_file)
    sessions.close("default")
    assert bridge.servers() == []
    assert not ca_file.exists()


# ---- Stop Appium ----


def test_stop_without_an_alias_stops_the_last_one(client, keywords):
    phone = keywords.start_appium("appium")
    tablet = keywords.start_appium("tablet")
    keywords.start_appium("appium")  # starting it again makes it the last one used
    keywords.stop_appium()
    assert not Path(phone.ca_file).exists()
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    keywords.stop_appium()
    assert not Path(tablet.ca_file).exists()
    assert bridge.servers() == []


def test_stop_by_alias(client, keywords):
    keywords.start_appium("appium", alias="phone")
    keywords.start_appium("tablet")
    keywords.stop_appium("phone")
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    with pytest.raises(Failure, match=r"no Appium server 'phone' is running; running: tablet"):
        keywords.stop_appium("phone")


def test_stop_with_none_running(client, keywords):
    keywords.stop_appium()  # nothing running: a no-op
    with pytest.raises(Failure, match=r"no Appium server 'appium' is running; running: none"):
        keywords.stop_appium("appium")


# ---- Device Hub (Appium on this machine) ----


def test_device_hub_appium_returns_the_same_shape(client, keywords, tmp_path):
    xctestrun = tmp_path / "WDA.xctestrun"
    appium = keywords.start_device_hub_appium("hub", str(xctestrun), appium="/opt/appium")
    assert HUB_CALLS == [("enter", str(xctestrun), "/opt/appium")]
    assert appium.url == "http://127.0.0.1:4723"
    assert appium.capabilities == {"platformName": "iOS", "appium:uuid": "IPHONE-1"}
    assert appium.ca_file is None and appium.wda_url == "http://127.0.0.1:8100"
    assert appium.alias == "hub" and appium.path == "hub" and appium.session == "default"
    assert keywords.start_device_hub_appium("hub", str(xctestrun)) == appium
    assert len(HUB_CALLS) == 1

    keywords.stop_appium("hub")
    assert HUB_CALLS[-1] == ("exit",)
    assert bridge.servers() == []


def test_upload_app_goes_to_a_device_hub_server(client, keywords, tmp_path):
    keywords.start_device_hub_appium("hub", str(tmp_path / "WDA.xctestrun"), alias="phone")
    ipa = tmp_path / "Demo.ipa"
    ipa.write_bytes(b"ipa")
    # By driver path or alias: the Device Hub target issues its own tokens.
    assert keywords.upload_app("hub", str(ipa)) == "jumpstarter-app:hub/Demo.ipa"
    assert keywords.upload_app("phone", str(ipa)) == "jumpstarter-app:hub/Demo.ipa"
    assert HUB_CALLS[1:] == [("upload", "Demo.ipa"), ("upload", "Demo.ipa")]
    with pytest.raises(Failure, match="does not exist"):
        keywords.upload_app("phone", str(tmp_path / "missing.ipa"))
    # The exporter's Appium still takes uploads without a running server.
    assert keywords.upload_app("appium", str(_apk(tmp_path))).endswith("/demo.apk")
    keywords.stop_appium("phone")


def test_device_hub_needs_an_ios_device(client, keywords, tmp_path):
    with pytest.raises(Failure, match="not an iOS device driver with Device Hub"):
        keywords.start_device_hub_appium("appium", str(tmp_path / "WDA.xctestrun"))
    assert bridge.servers() == []


# ---- Upload App ----


def test_upload_app_returns_a_token(client, keywords, tmp_path, monkeypatch):
    token = keywords.upload_app("appium", str(_apk(tmp_path)))
    assert token.startswith(APP_TOKEN_PREFIX) and token.endswith("/demo.apk")

    bundle = tmp_path / "Demo.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_text("<plist/>")
    assert keywords.upload_app("appium", str(bundle)).endswith("/Demo.app.zip")

    monkeypatch.chdir(tmp_path)
    assert keywords.upload_app("appium", "demo.apk").endswith("/demo.apk")
    assert bridge.servers() == []  # uploading does not start a server


def test_upload_app_failures(client, keywords, tmp_path):
    with pytest.raises(Failure, match="application '.*missing.apk' does not exist"):
        keywords.upload_app("appium", str(tmp_path / "missing.apk"))
    with pytest.raises(Failure, match="has no upload_app"):
        keywords.upload_app("power", str(_apk(tmp_path)))
    with pytest.raises(ValueError, match="plain names ending in"):
        keywords.upload_app("appium", str(_apk(tmp_path, "script.sh")))


# ---- in Robot ----

# Records how ``Open Application`` was called, in place of AppiumLibrary.
STUB_LIBRARY = """
import json

OPENED = []


def open_application(remote_url, alias=None, **capabilities):
    OPENED.append({"remote_url": remote_url, "alias": alias, "capabilities": capabilities})
    return len(OPENED)


def opened_as_json():
    return json.dumps(OPENED)
"""

STUB_SUITE = """
*** Settings ***
Library    OperatingSystem
Library    Collections
Library    jumpstarter_robot.Jumpstarter
Library    ${CURDIR}/stub_appium.py

*** Test Cases ***
Open Application Takes What Start Appium Returns
    ${appium}=    Start Appium    appium
    Should Start With    ${appium.url}    https://127.0.0.1:
    File Should Exist    ${appium.ca_file}
    ${token}=    Upload App    appium    ${APP}
    ${index}=    Open Application    ${appium.url}    &{appium.capabilities}
    ...    appium:appPackage=com.android.settings    appium:app=${token}
    Should Be Equal As Integers    ${index}    1
    ${opened}=    Opened As Json
    ${opened}=    Evaluate    json.loads($opened)    modules=json
    ${call}=    Get From List    ${opened}    0
    Should Be Equal    ${call}[remote_url]    ${appium.url}
    Should Be Equal    ${call}[capabilities][platformName]    Android
    Should Be Equal    ${call}[capabilities][appium:uuid]    emu-1
    Should Be Equal    ${call}[capabilities][appium:appPackage]    com.android.settings
    Should Be Equal    ${call}[capabilities][appium:app]    ${token}
    Stop Appium
    File Should Not Exist    ${appium.ca_file}

Clear Failures
    Run Keyword And Expect Error    *no Appium server 'nope' is running*    Stop Appium    nope
    Run Keyword And Expect Error    *is PowerClient, not an Appium driver*    Start Appium    power
    Run Keyword And Expect Error    *application '/no/such.apk' does not exist*    Upload App    appium    /no/such.apk

Server Ends With The Run
    Start Appium    tablet    alias=kept
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


def test_robot_suite_opens_applications_with_start_appium(client, tmp_path):
    (tmp_path / "stub_appium.py").write_text(textwrap.dedent(STUB_LIBRARY))
    result = _run(tmp_path, STUB_SUITE, APP=_apk(tmp_path))
    assert {test.name: test.status for test in result.suite.all_tests} == {
        "Open Application Takes What Start Appium Returns": "PASS",
        "Clear Failures": "PASS",
        "Server Ends With The Run": "PASS",
    }
    assert bridge.servers() == []  # the Jumpstarter library closed it at the end of the run


REAL_SUITE = """
*** Settings ***
Library    AppiumLibrary
Library    jumpstarter_robot.Jumpstarter

*** Test Cases ***
AppiumLibrary Opens A Session On The Served Url
    ${appium}=    Start Appium    appium
    Open Application    ${appium.url}    &{appium.capabilities}    appium:appPackage=com.android.settings
    ${session}=    Get Appium SessionId
    Should Be Equal    ${session}    s1
    Close All Applications
    Stop Appium
"""


def test_robot_suite_with_appium_library(client, tmp_path):
    """AppiumLibrary's own ``Open Application`` reaches the fake Appium behind the exporter."""
    pytest.importorskip("AppiumLibrary")
    _run(tmp_path, REAL_SUITE)
    assert bridge.servers() == []
