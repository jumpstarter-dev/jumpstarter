"""Tests for the Appium keywords.

The exporter side is the real ``AppiumDriver`` running the Appium package's
fake ``appium`` launcher, so ``serve()``, the HTTPS listener, its CA and
uploads are all real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import robot
from jumpstarter_driver_appium.common import APP_TOKEN_PREFIX
from jumpstarter_driver_appium.driver import AppiumDriver
from jumpstarter_driver_appium.testing import write_fake_appium
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from robot.api import ExecutionResult, Failure

from .. import context
from ..testing import use_client
from . import appium as bridge
from .appium import AppiumKeywords
from jumpstarter.common.utils import serve

FIXED = {"platformName": "Android", "appium:uuid": "emu-1"}


# ---- a Device Hub stand-in: an iOS device client whose Appium runs on this machine ----

HUB_CALLS: list[tuple] = []


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


# ---- Start Appium ----


def test_start_returns_the_url_and_capabilities(client, keywords):
    appium = keywords.start_appium("appium")
    assert appium.url.startswith("https://127.0.0.1:")
    assert appium.capabilities == FIXED
    assert appium.alias == "appium" and appium.path == "appium" and appium.session == "default"
    assert Path(appium.ca_file).is_file()
    # the DotDict reads like ${appium.url} and &{appium.capabilities} in Robot
    assert appium["url"] == appium.url and appium.capabilities["appium:uuid"] == "emu-1"


def test_starting_again_returns_the_running_server(client, keywords):
    first = keywords.start_appium("appium")
    second = keywords.start_appium("appium")
    assert second == first
    assert len(bridge.servers()) == 1


def test_start_needs_an_appium_driver(client, keywords):
    with pytest.raises(Failure, match=r"'power' .* is PowerClient, not an Appium driver \(it has no serve\(\)\)"):
        keywords.start_appium("power")
    with pytest.raises(Failure, match="'nope'.* is not present"):
        keywords.start_appium("nope")
    assert bridge.servers() == []


# ---- aliases ----


# ---- scopes and sessions ----


def test_servers_end_with_their_scope(client, keywords):
    keywords.start_appium("appium", scope="suite")
    keywords.start_appium("tablet", scope="global")
    context.end_suite("s1")  # started outside a suite, so any suite end closes it
    assert [s.alias for s in bridge.servers()] == ["tablet"]
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.start_appium("appium", scope="forever")
    assert [s.alias for s in bridge.servers()] == ["tablet"]


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


# ---- Device Hub (Appium on this machine) ----


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
