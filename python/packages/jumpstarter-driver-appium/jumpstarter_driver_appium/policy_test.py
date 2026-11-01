import json
from pathlib import Path

import pytest

from .common import APP_TOKEN_PREFIX, AppiumTarget
from .policy import AppiumPolicy, PolicyError

TOKEN = f"{APP_TOKEN_PREFIX}abc/Demo.apk"


def resolve(token):
    if token != TOKEN:
        raise PolicyError("unknown")
    return "/lease/Demo.apk"


def policy(fixed=None):
    return AppiumPolicy(fixed or {"appium:udid": "D"}, resolve)


def forward(method, path, body):
    return json.loads(policy().request(method, path, json.dumps(body).encode()))


def test_non_json_and_bodiless_requests():
    assert policy().request("GET", "/status", b"") == b""
    assert policy().request("DELETE", "/session/s1", b"") == b""
    with pytest.raises(PolicyError, match="JSON"):
        policy().request("POST", "/session/s1/url", b"not json")
    with pytest.raises(PolicyError, match="objects"):
        policy().request("POST", "/session/s1/url", b"[]")


def test_new_session_merges_and_drops_legacy_capabilities():
    result = forward(
        "POST",
        "/session",
        {"capabilities": {"alwaysMatch": {"appium:app": TOKEN}}, "desiredCapabilities": {"app": "/etc"}},
    )
    assert result == {
        "capabilities": {"alwaysMatch": {"appium:app": "/lease/Demo.apk", "appium:udid": "D"}, "firstMatch": [{}]}
    }


def test_boolean_url_capabilities_are_allowed():
    always = forward("POST", "/session", {"capabilities": {"alwaysMatch": {"webSocketUrl": True}}})
    assert always["capabilities"]["alwaysMatch"]["webSocketUrl"] is True


@pytest.mark.parametrize(
    "capabilities",
    [
        {"alwaysMatch": []},
        {"firstMatch": []},
        {"firstMatch": ["x"]},
        {"alwaysMatch": {"appium:options": []}},
        {"alwaysMatch": {"appium:noReset": True, "appium:options": {"noReset": False}}},
    ],
)
def test_malformed_sessions(capabilities):
    with pytest.raises(PolicyError):
        forward("POST", "/session", {"capabilities": capabilities})


@pytest.mark.parametrize("path", ["/Session", "/SESSION/", "/session?x=1", "//session", "/%73ession"])
def test_new_session_checks_apply_to_every_spelling_of_the_route(path):
    # Appium's Express router matches these as POST /session.
    with pytest.raises(PolicyError, match="proxy"):
        forward("POST", path, {"capabilities": {"alwaysMatch": {"proxy": {"proxyType": "manual"}}}})
    created = forward("POST", path, {"capabilities": {"alwaysMatch": {"appium:udid": "OTHER"}}})
    assert created["capabilities"]["alwaysMatch"]["appium:udid"] == "D"


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_json_numbers_are_policy_errors(constant):
    with pytest.raises(PolicyError, match="not a JSON number"):
        policy().request("POST", "/session/s1/execute/sync", b'{"args": [' + constant + b"]}")


def test_bodies_too_deep_to_parse_are_policy_errors():
    with pytest.raises(PolicyError, match="deeply"):
        policy().request("POST", "/session/s1/execute/sync", b'{"a":' * 100_000 + b"1" + b"}" * 100_000)


def test_nested_arguments_are_checked():
    nested = {"args": [{"options": {"deep": [{"remotePath": "http://x"}]}}]}
    with pytest.raises(PolicyError, match="remotePath"):
        forward("POST", "/session/s1/execute/sync", nested)
    deep = {}
    for _ in range(80):
        deep = {"a": deep}
    with pytest.raises(PolicyError, match="deeply"):
        forward("POST", "/session/s1/execute/sync", deep)
    assert forward("POST", "/session/s1/appium/device/install_app", {"appPath": TOKEN}) == {
        "appPath": "/lease/Demo.apk"
    }


def test_target_environment_extends_node_trust(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("LEASE CA\n")
    existing = tmp_path / "corp.pem"
    # certifi-style bundles name issuers such as "Főtanúsítvány" in comments.
    existing.write_text("# Főtanúsítvány\nCORP CA\n", encoding="utf-8")
    target = AppiumTarget(url="https://127.0.0.1:1", default_capabilities={"appium:udid": "D"}, ca_file=ca)
    env = target.environment({"NODE_EXTRA_CA_CERTS": str(existing)})
    assert Path(env["NODE_EXTRA_CA_CERTS"]).read_text(encoding="utf-8") == "# Főtanúsítvány\nCORP CA\n\nLEASE CA\n"
    assert existing.read_text(encoding="utf-8") == "# Főtanúsítvány\nCORP CA\n"
    with pytest.raises(ValueError, match="disables"):
        target.environment({"NODE_TLS_REJECT_UNAUTHORIZED": "0"})
    copy = target.capabilities()
    copy["appium:udid"] = "changed"
    assert target.capabilities()["appium:udid"] == "D"
    ca.unlink()
    with pytest.raises(RuntimeError, match="closed"):
        target.ssl_context()


def test_plain_http_target():
    target = AppiumTarget(url="http://127.0.0.1:4723", wda_url="http://[fd00::1]:8100")
    assert target.ssl_context() is None
    assert target.environment({})["JUMPSTARTER_APPIUM_WDA_URL"] == "http://[fd00::1]:8100"
