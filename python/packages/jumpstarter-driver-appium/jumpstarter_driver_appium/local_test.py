import json
import urllib.request

import pytest

from .local import local_appium


def test_local_appium_serves_on_loopback_and_stops(fake_appium):
    executable, record = fake_appium
    with local_appium(str(executable), env={"FAKE_APPIUM_RECORD": str(record), "APPIUM_HOME": "/users/home"}) as url:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(f"{url}/status", timeout=5) as response:
            assert json.load(response)["value"]["ready"] is True
    (started,) = [json.loads(line) for line in record.read_text().splitlines()]
    assert started["env"]["APPIUM_HOME"] == "/users/home"
    assert started["args"][started["args"].index("--address") + 1] == "127.0.0.1"
    with pytest.raises(OSError):
        urllib.request.urlopen(f"{url}/status", timeout=2)


def test_readiness_probe_ignores_proxy_settings(fake_appium, monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    executable, record = fake_appium
    with local_appium(str(executable), env={"FAKE_APPIUM_RECORD": str(record)}, timeout=10) as url:
        assert url.startswith("http://127.0.0.1:")


def test_local_appium_reports_startup_failure(fake_appium, monkeypatch):
    monkeypatch.setenv("FAKE_APPIUM_FAIL", "1")
    with pytest.raises(RuntimeError, match="exited:\nfake appium refused to start"), local_appium(str(fake_appium[0])):
        pass


def test_missing_appium_is_explained():
    with pytest.raises(RuntimeError, match="does not bundle"), local_appium("definitely-not-appium"):
        pass
