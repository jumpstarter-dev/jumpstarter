import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests.auth

from .driver import HttpAuthConfig, HttpDigestAuth, HttpEndpointConfig, HttpPower
from jumpstarter.common.utils import serve


class MockHTTPHandler(BaseHTTPRequestHandler):
    """Mock HTTP server handler for testing"""

    def log_message(self, format, *args):
        # Suppress server logs during testing
        pass

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def _handle_request(self):
        # Record the request for verification
        if not hasattr(self.server, 'requests'):
            self.server.requests = []

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else None

        self.server.requests.append({
            'method': self.command,
            'path': self.path,
            'body': body
        })

        # Send appropriate response based on endpoint
        if self.path == '/read':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"voltage": 12.0, "current": 2.5}')
        else:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')


def test_drivers_http_power():
    # Start a mock HTTP server
    server = HTTPServer(('localhost', 0), MockHTTPHandler)
    server.requests = []  # ty: ignore[unresolved-attribute]
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    try:
        base_url = f"http://localhost:{server_port}"

        instance = HttpPower(
            power_on=HttpEndpointConfig(url=f"{base_url}/on", method="POST", data="power=on"),
            power_off=HttpEndpointConfig(url=f"{base_url}/off", method="POST", data="power=off"),
            power_read=HttpEndpointConfig(url=f"{base_url}/read"),
        )

        with serve(instance) as client:
            # Test that the client can be created and basic methods exist
            assert hasattr(client, 'on')
            assert hasattr(client, 'off')
            assert hasattr(client, 'read')

            # Test actual HTTP calls
            client.on()
            client.off()

            # Test read method — parses the JSON the mock /read endpoint returns
            readings = list(client.read())
            assert len(readings) == 1
            assert readings[0].voltage == 12.0
            assert readings[0].current == 2.5

            # Verify HTTP requests were made
            assert len(server.requests) == 3  # ty: ignore[unresolved-attribute]

            # Check on request
            on_request = server.requests[0]  # ty: ignore[unresolved-attribute]
            assert on_request['method'] == 'POST'
            assert on_request['path'] == '/on'
            assert on_request['body'] == 'power=on'

            # Check off request
            off_request = server.requests[1]  # ty: ignore[unresolved-attribute]
            assert off_request['method'] == 'POST'
            assert off_request['path'] == '/off'
            assert off_request['body'] == 'power=off'

            # Check read request
            read_request = server.requests[2]  # ty: ignore[unresolved-attribute]
            assert read_request['method'] == 'GET'
            assert read_request['path'] == '/read'
            assert read_request['body'] is None

    finally:
        server.shutdown()
        server_thread.join(timeout=1)


def _power(power_read):
    return HttpPower(
        power_on=HttpEndpointConfig(url="http://x/on"),
        power_off=HttpEndpointConfig(url="http://x/off"),
        power_read=power_read,
    )


def test_read_parses_nested_paths():
    drv = _power(
        HttpEndpointConfig(
            url="http://x/read", voltage_path="emeter.voltage", current_path="emeter.current"
        )
    )
    drv._make_http_request = lambda cfg: '{"emeter": {"voltage": 231.0, "current": 0.45}}'
    reading = next(iter(drv.read()))
    assert reading.voltage == 231.0
    assert reading.current == 0.45


def test_read_missing_default_key_is_zero():
    drv = _power(HttpEndpointConfig(url="http://x/read"))
    drv._make_http_request = lambda cfg: '{"voltage": 230.0}'  # device reports no current
    reading = next(iter(drv.read()))
    assert reading.voltage == 230.0
    assert reading.current == 0.0


def test_read_configured_path_missing_raises():
    drv = _power(HttpEndpointConfig(url="http://x/read", voltage_path="nope.here"))
    drv._make_http_request = lambda cfg: '{"voltage": 1.0}'
    with pytest.raises(ValueError, match="not found in read response"):
        list(drv.read())


def test_read_non_numeric_list_index_raises_not_found():
    drv = _power(HttpEndpointConfig(url="http://x/read", voltage_path="meters.x.voltage"))
    drv._make_http_request = lambda cfg: '{"meters": [{"voltage": 1.0}]}'
    with pytest.raises(ValueError, match="not found in read response"):
        list(drv.read())


def test_read_non_json_raises():
    drv = _power(HttpEndpointConfig(url="http://x/read"))
    drv._make_http_request = lambda cfg: "OK"
    with pytest.raises(ValueError, match="did not return JSON"):
        list(drv.read())


def test_read_without_endpoint_raises():
    drv = _power(None)
    with pytest.raises(ValueError, match="not configured"):
        list(drv.read())


CHALLENGE = 'Digest realm="r", nonce="n", qop="auth", algorithm=MD5'
# Without qop the client adds no cnonce, so the response is fully determined by the
# challenge and the credentials — which is what lets the test below pin a fixed value.
NO_QOP_CHALLENGE = 'Digest realm="r", nonce="n", algorithm=MD5'


class AuthHandler(BaseHTTPRequestHandler):
    """Records Authorization headers; /digest paths demand a digest handshake first."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization")
        self.server.auth_headers.append(auth)
        if self.path.startswith("/digest") and not (auth or "").startswith("Digest "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", NO_QOP_CHALLENGE if "noqop" in self.path else CHALLENGE)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


@contextmanager
def _auth_server():
    server = HTTPServer(("localhost", 0), AuthHandler)
    server.auth_headers = []  # ty: ignore[unresolved-attribute]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://localhost:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_dict_config_is_reconstructed():
    """Exporter YAML arrives as plain dicts, which __post_init__ has to rebuild into the config types."""
    config = {
        "power_on": {"url": "http://x/on"},
        "power_off": {"url": "http://x/off"},
        "power_read": {"url": "http://x/read", "voltage_path": "emeter.voltage"},
        "auth": {"digest": {"user": "u", "password": "p"}},
    }
    drv = HttpPower(**config)
    assert isinstance(drv.power_on, HttpEndpointConfig)
    assert isinstance(drv.power_off, HttpEndpointConfig)
    assert isinstance(drv.power_read, HttpEndpointConfig)
    assert drv.power_read.voltage_path == "emeter.voltage"
    assert isinstance(drv.auth, HttpAuthConfig)
    assert isinstance(drv.auth.digest, HttpDigestAuth)
    assert drv.auth.digest.user == "u"


def _auth_power(auth, on_url="http://x/on", off_url="http://x/off"):
    return HttpPower(
        power_on=HttpEndpointConfig(url=on_url),
        power_off=HttpEndpointConfig(url=off_url),
        auth=auth,
    )


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        ({"basic": {"user": "u", "password": "p"}}, "Basic dTpw"),
        ({"digest": {"user": "u", "password": "p"}}, 'Digest username="u"'),
    ],
    ids=["basic", "digest"],
)
def test_auth_sends_credentials(auth, expected):
    with _auth_server() as (server, base_url):
        _auth_power(auth, f"{base_url}/{next(iter(auth))}").on()
        assert expected in server.auth_headers[-1]


def test_digest_challenge_is_skipped_after_the_first_request():
    with _auth_server() as (server, base_url):
        drv = _auth_power({"digest": {"user": "u", "password": "p"}}, f"{base_url}/digest/on", f"{base_url}/digest/off")
        drv.on()
        drv.off()
        # Only the very first request is challenged: the cached handler keeps the negotiated
        # nonce, so the second endpoint on the same origin authenticates without another 401.
        challenged = [header is None for header in server.auth_headers]
        assert challenged == [True, False, False], server.auth_headers


# RFC 2069: response = MD5(HA1:nonce:HA2) with HA1 = MD5("u:r:p") and HA2 = MD5("GET:/digest-noqop").
# Precomputed from the RFC by hand so the test is an independent oracle, not a restatement
# of the implementation's own arithmetic.
EXPECTED_DIGEST_RESPONSE = "c4fc7e43cb6786ea4121bda32e36f196"


def test_digest_response_matches_a_known_value():
    with _auth_server() as (server, base_url):
        _auth_power({"digest": {"user": "u", "password": "p"}}, f"{base_url}/digest-noqop").on()
        header = server.auth_headers[-1] or ""
        match = re.search(r'response="([0-9a-f]{32})"', header)
        assert match is not None, f"no digest response in {header!r}"
        assert match[1] == EXPECTED_DIGEST_RESPONSE


@pytest.mark.parametrize(
    ("scheme", "handler"),
    [("basic", requests.auth.HTTPBasicAuth), ("digest", requests.auth.HTTPDigestAuth)],
)
def test_build_auth_selects_handler(scheme, handler):
    auth = {scheme: {"user": "u", "password": "p"}}
    assert isinstance(_auth_power(auth)._build_auth("http://x/on"), handler)


def test_build_auth_without_credentials():
    assert _auth_power(None)._build_auth("http://x/on") is None
    assert _auth_power({})._build_auth("http://x/on") is None


@pytest.mark.parametrize(
    ("scheme", "handler"),
    [("basic", requests.auth.HTTPBasicAuth), ("digest", requests.auth.HTTPDigestAuth)],
)
def test_empty_auth_block_is_still_configured(scheme, handler):
    # An empty mapping is falsy but still a configured block, so it must be deserialized
    assert isinstance(_auth_power({scheme: {}})._build_auth("http://x/on"), handler)


@pytest.mark.parametrize("basic", [{"user": "u", "password": "p"}, {}], ids=["populated", "empty"])
def test_basic_and_digest_are_mutually_exclusive(basic):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _auth_power({"basic": basic, "digest": {"user": "u", "password": "p"}})


def test_digest_handler_is_reused_per_origin():
    drv = _auth_power({"digest": {"user": "u", "password": "p"}})
    # One handler per origin keeps the negotiated nonce, so repeat requests skip the 401.
    assert drv._build_auth("http://x/on") is drv._build_auth("http://x/off")
    assert drv._build_auth("http://x/on") is not drv._build_auth("http://y/on")
