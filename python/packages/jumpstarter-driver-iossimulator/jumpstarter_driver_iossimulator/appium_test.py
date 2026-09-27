import concurrent.futures
import errno
import json
import ssl
import sys
import threading
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import h11
import pytest
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group, fail_after, sleep, to_thread
from anyio.streams.tls import TLSStream
from jumpstarter_driver_iosdevice.https import HttpsIdentity, https_endpoint

from . import appium as module
from .appium import AppiumPolicy, AppiumProvider, PolicyError, decode_json
from .http_service import SimulatorHttpContext

FIXED = {
    "platformName": "iOS",
    "appium:automationName": "XCUITest",
    "appium:udid": "OWNED-UDID",
    "appium:platformVersion": "27.0",
    "appium:simulatorDevicesSetPath": "/private/owned/devices",
    "appium:prebuiltWDAPath": "/operator/WDA.app",
    "appium:wdaLocalPort": 12345,
    "appium:mjpegServerPort": 12346,
    "appium:isHeadless": False,
    "appium:noReset": True,
}
BUNDLE = "dev.jumpstarter.smoke"


def caps(extra=None):
    return {"capabilities": {"alwaysMatch": {"appium:bundleId": BUNDLE} | (extra or {}), "firstMatch": [{}]}}


@pytest.fixture
def policy():
    return AppiumPolicy(FIXED)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_capabilities_inject_identity_and_hide_host_paths(policy):
    result = policy.capabilities(caps({"platformName": "iOS", "appium:noReset": True}))
    assert result["capabilities"]["alwaysMatch"] == FIXED | {"appium:bundleId": BUNDLE}
    assert not any("Path" in key or "Port" in key for key in policy.public)


@pytest.mark.parametrize(
    "key,value",
    [
        ("appium:udid", "OTHER"),
        ("appium:simulatorDevicesSetPath", "/elsewhere"),
        ("appium:wdaLocalPort", 8100),
        ("appium:app", "/etc/private"),
        ("appium:app", "https://example.invalid/app.ipa"),
        ("appium:webDriverAgentUrl", "http://localhost:22"),
        ("appium:otherApps", ["/secret"]),
        ("appium:processArguments", {"env": {"DYLD_INSERT_LIBRARIES": "/tmp/x"}}),
        ("appium:shutdownOtherSimulators", True),
        ("appium:isHeadless", True),
        ("appium:customSSLCert", "secret"),
        ("appium:derivedDataPath", "/tmp"),
        ("appium:enforceFreshSimulatorCreation", True),
        ("browserName", "Safari"),
        ("appium:settings", {"arbitrary": True}),
        ("appium:noReset", 1),
    ],
)
@pytest.mark.parametrize("location", ["always", "first", "options"])
def test_unsafe_capabilities_rejected(policy, key, value, location):
    body = caps()
    if location == "always":
        body["capabilities"]["alwaysMatch"][key] = value
    elif location == "first":
        body["capabilities"]["firstMatch"] = [{key: value}]
    else:
        body["capabilities"]["alwaysMatch"]["appium:options"] = {key.removeprefix("appium:"): value}
    with pytest.raises(PolicyError):
        policy.capabilities(body)


def test_options_and_single_first_match_are_supported(policy):
    body = {
        "capabilities": {
            "alwaysMatch": {"platformName": "iOS"},
            "firstMatch": [{"appium:options": {"bundleId": BUNDLE, "forceAppLaunch": True}}],
        }
    }
    value = policy.capabilities(body)["capabilities"]["alwaysMatch"]
    assert value["appium:bundleId"] == BUNDLE and value["appium:forceAppLaunch"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"desiredCapabilities": {"udid": "other"}},
        {"capabilities": {"alwaysMatch": {}}},
        {"capabilities": {"alwaysMatch": {"appium:bundleId": BUNDLE}, "firstMatch": [{}, {}]}},
        {"capabilities": {"alwaysMatch": {"appium:bundleId": BUNDLE}, "firstMatch": [{"appium:bundleId": BUNDLE}]}},
        {"capabilities": {"alwaysMatch": {"appium:bundleId": BUNDLE, "appium:options": {"bundleId": BUNDLE}}}},
    ],
)
def test_ambiguous_or_legacy_caps_rejected(policy, body):
    with pytest.raises(PolicyError):
        policy.capabilities(body)


@pytest.mark.parametrize(
    "script",
    [
        "mobile: installApp",
        "mobile: pushFile",
        "mobile: pullFile",
        "mobile: deleteFile",
        "mobile: runXCTest",
        "mobile: startPerfRecord",
        "mobile: installCertificate",
        "mobile: shell",
        "return 1",
    ],
)
def test_mobile_host_and_install_operations_rejected(policy, script):
    with pytest.raises(PolicyError):
        policy.request("POST", "/session/owned/execute/sync", {"script": script, "args": [{}]}, "owned")


@pytest.mark.parametrize(
    "data", [{"bundleId": BUNDLE, "arguments": ["untrusted"]}, {"bundleId": BUNDLE, "environment": {"PATH": "/tmp"}}]
)
def test_mobile_launch_options_rejected(policy, data):
    with pytest.raises(PolicyError):
        policy.request("POST", "/session/owned/execute/sync", {"script": "mobile: launchApp", "args": [data]}, "owned")


@pytest.mark.parametrize(
    ("script", "args"),
    [
        ("mobile: isKeyboardShown", []),
        ("mobile: hideKeyboard", [{}]),
        ("mobile: hideKeyboard", [{"keys": ["Done"]}]),
        ("mobile: backgroundApp", [{"seconds": 1}]),
    ],
)
def test_python_client_keyboard_and_background_helpers(policy, script, args):
    command = {"script": script, "args": args}
    assert policy.request("POST", "/session/owned/execute/sync", command, "owned") == command


@pytest.mark.parametrize(
    ("script", "data"),
    [
        ("mobile: isKeyboardShown", {"x": 1}),
        ("mobile: hideKeyboard", {"keys": ["k"] * 9}),
        ("mobile: hideKeyboard", {"keys": "Done"}),
        ("mobile: backgroundApp", {"seconds": 601}),
        ("mobile: backgroundApp", {}),
        ("mobile: launchApp", {}),
    ],
)
def test_mobile_helper_arguments_are_bounded(policy, script, data):
    with pytest.raises(PolicyError):
        policy.request("POST", "/session/owned/execute/sync", {"script": script, "args": [data]}, "owned")
    with pytest.raises(PolicyError):
        policy.request("POST", "/session/owned/execute/sync", {"script": script, "args": [{}, {}]}, "owned")


@pytest.mark.parametrize(
    "script", ["mobile: activateApp", "mobile: terminateApp", "mobile: queryAppState", "mobile: isAppInstalled"]
)
def test_python_client_app_id_is_accepted_only_when_it_matches(policy, script):
    # Appium-Python-Client sends appId next to bundleId for cross-platform helpers.
    command = {"script": script, "args": [{"appId": BUNDLE, "bundleId": BUNDLE}]}
    forwarded = policy.request("POST", "/session/owned/execute/sync", command, "owned")
    assert forwarded == {"script": script, "args": [{"bundleId": BUNDLE}]}
    for data in (
        {"appId": "other.app", "bundleId": BUNDLE},
        {"appId": BUNDLE},
        {"appId": BUNDLE, "bundleId": BUNDLE, "x": 1},
    ):
        with pytest.raises(PolicyError):
            policy.request("POST", "/session/owned/execute/sync", {"script": script, "args": [data]}, "owned")


@pytest.mark.parametrize(
    "path",
    [
        "/session/other/source",
        "/session/owned2/source",
        "/sessions",
        "/session/owned/appium/device/install_app",
        "/session/owned/context",
        "/session/owned/url",
        "/session/owned/execute/async",
        "/session/owned/se/file",
    ],
)
def test_unknown_cross_session_routes_rejected(policy, path):
    with pytest.raises(PolicyError):
        policy.request("POST", path, {}, "owned")


def test_supported_native_routes_and_gestures(policy):
    assert policy.request("GET", "/session/owned/source", {}, "owned") == {}
    command = {"script": "mobile: launchApp", "args": [{"bundleId": BUNDLE}]}
    assert policy.request("POST", "/session/owned/execute/sync", command, "owned") == command
    assert policy.request("POST", "/session/owned/element", {"using": "accessibility id", "value": "button"}, "owned")
    actions = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "x": 100, "y": 100, "duration": 0},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        ]
    }
    assert policy.request("POST", "/session/owned/actions", actions, "owned") == actions


def test_body_parser_rejects_duplicate_keys_and_nonobjects():
    for data in (b'{"caps":{},"caps":{}}', b"[]", b"null", b"{"):
        with pytest.raises(PolicyError):
            decode_json(data)


@pytest.fixture
def fake_executable(tmp_path):
    executable = tmp_path / "fake-appium"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import http.server,json,os,ssl,sys
from pathlib import Path
args=sys.argv
root=Path.cwd()
(root/'environment.json').write_text(json.dumps({'HOME':os.environ['HOME'],'APPIUM_HOME':os.environ['APPIUM_HOME']}))
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send(self,value):
  data=json.dumps({'value':value}).encode(); self.send_response(200)
  self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
 def do_GET(self): self.send({'ready':True} if self.path=='/status' else 'native-source')
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  with (root/'requests.jsonl').open('a') as out: out.write(json.dumps({'path':self.path,'body':body})+'\\n')
  value={'sessionId':'owned-session','capabilities':body['capabilities']['alwaysMatch']}
  self.send(value if self.path=='/session' else None)
 def do_DELETE(self): self.send(None)
server=http.server.ThreadingHTTPServer(('127.0.0.1',int(args[args.index('--port')+1])),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(args[args.index('--ssl-cert-path')+1],args[args.index('--ssl-key-path')+1])
server.socket=ctx.wrap_socket(server.socket,server_side=True)
server.serve_forever()
"""
    )
    executable.chmod(0o700)
    return executable


def make_service(tmp_path, fake_executable, **kwargs):
    home, wda = tmp_path / "extensions", tmp_path / "WDA.app"
    home.mkdir(exist_ok=True)
    wda.mkdir(exist_ok=True)
    context = SimulatorHttpContext(
        directory=tmp_path, device_set=tmp_path / "devices", udid="OWNED-UDID", platform_version="27.0"
    )
    return AppiumProvider(
        context=context,
        executable=str(fake_executable),
        home=str(home),
        wda=str(wda),
        startup_timeout=3,
        request_timeout=2,
        **kwargs,
    )


@pytest.fixture
def service(tmp_path, fake_executable):
    service = make_service(tmp_path, fake_executable)
    yield service
    service.close()


def test_private_environment_tls_keys_and_process_cleanup(service):
    directory, process = service.directory, service.process
    env = json.loads((directory / "environment.json").read_text())
    assert Path(env["HOME"]).is_relative_to(directory)
    assert Path(env["APPIUM_HOME"]) != Path(env["HOME"])
    assert not (directory / "backend.key").exists()
    assert not (directory / "backend.crt").exists()
    config = json.loads((directory / "config.json").read_text())["server"]
    assert config["use-plugins"] == [] and config["relaxed-security"] is False
    assert config["deny-insecure"] == ["*:*"]
    with pytest.raises(ssl.SSLCertVerificationError):
        connection = module.http.client.HTTPSConnection("127.0.0.1", service.port, timeout=2)
        try:
            connection.request("GET", "/status")
        finally:
            connection.close()
    service.close()
    assert process.poll() is not None and not directory.exists()
    with pytest.raises(RuntimeError):
        service.metadata()


def test_sessions_are_scoped_serial_and_response_paths_removed(service):
    status, result = service.request("POST", "/session", caps())
    assert status == 200 and result["value"]["sessionId"] == "owned-session"
    assert "appium:prebuiltWDAPath" not in result["value"]["capabilities"]
    request = json.loads((service.directory / "requests.jsonl").read_text().splitlines()[0])
    assert request["body"]["capabilities"]["alwaysMatch"]["appium:udid"] == "OWNED-UDID"
    with pytest.raises(PolicyError):
        service.request("POST", "/session", caps())
    with pytest.raises(PolicyError):
        service.request("DELETE", "/session/unrelated", {})
    service.request("DELETE", "/session/owned-session", {})
    with pytest.raises(PolicyError):
        service.request("GET", "/session/owned-session/source", {})
    assert service.request("POST", "/session", caps())[0] == 200


def test_concurrent_session_creation_rejected(service):
    def create():
        try:
            return service.request("POST", "/session", caps())[0]
        except PolicyError:
            return "rejected"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert sorted(results, key=str) == [200, "rejected"]


async def exchange(service, request):
    async with https_endpoint(service.identity, service.handle) as raw:
        context = ssl.create_default_context(cadata=service.identity.certificate)
        async with await TLSStream.wrap(
            raw, hostname="127.0.0.1", ssl_context=context, standard_compatible=False
        ) as stream:
            await stream.send(request)
            connection = h11.Connection(h11.CLIENT)
            data = bytearray()
            status = None
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    connection.receive_data(await stream.receive())
                elif isinstance(event, h11.Response):
                    status = event.status_code
                elif isinstance(event, h11.Data):
                    data.extend(event.data)
                elif isinstance(event, h11.EndOfMessage):
                    return status, json.loads(data)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "wire_request",
    [
        b"GET /%73tatus HTTP/1.1\r\nHost: test\r\n\r\n",
        b"GET /status?target=localhost HTTP/1.1\r\nHost: test\r\n\r\n",
        b"GET https://localhost/status HTTP/1.1\r\nHost: test\r\n\r\n",
        b"POST /session HTTP/1.1\r\nHost: test\r\nContent-Length: 1048577\r\n\r\n",
        b"POST /session HTTP/1.1\r\nHost: test\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"POST /session HTTP/1.1\r\nHost: test\r\nExpect: 100-continue\r\nContent-Length: 0\r\n\r\n",
        b"GET /status HTTP/1.1\r\nHost: test\r\nContent-Length: 2\r\n\r\n{}",
    ],
)
async def test_invalid_http_does_not_reach_backend(service, wire_request):
    with fail_after(5):
        status, _ = await exchange(service, wire_request)
    assert status == 400
    assert not (service.directory / "requests.jsonl").exists()


@pytest.mark.anyio
async def test_verified_tls_gateway_creates_native_session(service):
    body = json.dumps(caps()).encode()
    request = (
        b"POST /session HTTP/1.1\r\nHost: attacker.example\r\nAuthorization: ignored\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body
    )
    with fail_after(5):
        status, value = await exchange(service, request)
    assert status == 200 and value["value"]["sessionId"] == "owned-session"


def test_backend_port_reuse_with_wrong_certificate_fails_closed(service):
    other = HttpsIdentity.generate()
    service._context = ssl.create_default_context(cadata=other.certificate)
    with pytest.raises(module.ServiceError) as error:
        service.request("POST", "/session", caps())
    assert isinstance(error.value.__cause__, ssl.SSLCertVerificationError)
    assert not service.directory.exists()


def test_startup_failure_removes_owned_material(tmp_path, fake_executable, monkeypatch):
    process = MagicMock()
    process.poll.return_value = 1
    process.pid = 999999999
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_, **__: process)
    with pytest.raises(RuntimeError, match="exited"):
        make_service(tmp_path, fake_executable)
    assert not list(tmp_path.glob("appium-*"))


@pytest.mark.parametrize(
    ("operation", "failure"),
    # A refused create is definite, not uncertain; see the refused-session test.
    [("create", "timeout"), ("create", "invalid-id"), ("delete", "timeout"), ("delete", "error")],
)
def test_uncertain_session_lifecycle_stops_service(service, monkeypatch, operation, failure):
    if operation == "delete":
        service.request("POST", "/session", caps())

    def backend(*args, **kwargs):
        if failure == "timeout":
            raise TimeoutError("backend timeout")
        if failure == "error":
            return 500, {"value": {"error": "unknown error"}}
        return 200, {"value": {"sessionId": "invalid/path"}}

    monkeypatch.setattr(service, "_backend", backend)
    with pytest.raises(module.ServiceError, match="power off/on"):
        service.request(
            "POST" if operation == "create" else "DELETE",
            "/session" if operation == "create" else "/session/owned-session",
            caps() if operation == "create" else {},
        )
    assert service.process is None and not service.directory.exists()


def test_refused_session_keeps_service_and_releases_reservations(service, monkeypatch):
    backend = service._backend

    def refuse(method, path, body, **kwargs):
        if path == "/session":
            detail = {"error": "session not created", "message": "No WDA at /Users/private/WDA.app", "stacktrace": "x"}
            return 500, {"value": detail}
        return backend(method, path, body, **kwargs)

    monkeypatch.setattr(service, "_backend", refuse)
    status, result = service.request("POST", "/session", caps())
    assert status == 500
    assert result == {
        "value": {
            "error": "session not created",
            "message": "Appium could not create the session; see the exporter log",
            "stacktrace": "",
        }
    }
    assert service._session is None and not service._reservations and service.process.poll() is None
    monkeypatch.setattr(service, "_backend", backend)
    status, result = service.request("POST", "/session", caps())
    assert status == 200 and result["value"]["sessionId"] == "owned-session"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("no such element", "no such element"),
        ("stale element reference", "stale element reference"),
        ("invented by a plugin", "unknown error"),
        (None, "unknown error"),
    ],
)
def test_command_errors_keep_only_standard_codes(service, monkeypatch, code, expected):
    service.request("POST", "/session", caps())
    detail = {"error": code, "message": "at /Users/private/source.js", "stacktrace": "trace"}
    monkeypatch.setattr(service, "_backend", lambda *args, **kwargs: (404, {"value": detail}))
    locator = {"using": "accessibility id", "value": "missing"}
    status, result = service.request("POST", "/session/owned-session/element", locator)
    assert status == 404
    assert result == {"value": {"error": expected, "message": f"Appium returned {expected}", "stacktrace": ""}}


def test_close_spares_only_the_kept_stream(service):
    loop, kept, other = MagicMock(), MagicMock(), MagicMock()
    service._connections = {kept: loop, other: loop}
    service.close(keep=kept)
    assert [call.args[0] for call in loop.call_soon_threadsafe.call_args_list] == [other.cancel]


@pytest.mark.anyio
async def test_uncertain_failure_answers_its_own_request_before_revoking_others(service, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("backend timeout")

    monkeypatch.setattr(service, "_backend", timeout)
    context = ssl.create_default_context(cadata=service.identity.certificate)
    idle_entered, idle_ended = threading.Event(), threading.Event()
    responses = []

    async def idle():
        try:
            async with service.connect() as raw:
                await TLSStream.wrap(raw, hostname="127.0.0.1", ssl_context=context, standard_compatible=False)
                idle_entered.set()
                await sleep(60)
        finally:
            idle_ended.set()

    async def create():
        body = json.dumps(caps()).encode()
        head = f"POST /session HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\n\r\n"
        async with service.connect() as raw:
            tls = await TLSStream.wrap(raw, hostname="127.0.0.1", ssl_context=context, standard_compatible=False)
            await tls.send(head.encode() + body)
            response = b""
            with suppress(EndOfStream, BrokenResourceError, ClosedResourceError):
                while True:
                    response += await tls.receive()
            responses.append(response)

    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(idle)
            while not idle_entered.is_set():
                await sleep(0.01)
            tasks.start_soon(create)
    assert responses[0].startswith(b"HTTP/1.1 502 ")
    assert b"power off/on to start a fresh service" in responses[0]
    assert idle_ended.is_set() and service.process is None


def test_startup_failure_names_node_requirement_and_keeps_log_exporter_side(tmp_path, caplog):
    executable = tmp_path / "broken-appium"
    executable.write_text(
        f"#!{sys.executable}\n"
        "print('Could not configure Appium server. Original error: No such module: http_parser (/Users/private)')\n"
        "raise SystemExit(1)\n"
    )
    executable.chmod(0o700)
    with pytest.raises(module.ServiceError) as error:
        make_service(tmp_path, executable)
    message = str(error.value)
    assert "exited before readiness" in message and "requires Node.js 22" in message
    assert "/Users/private" not in message
    assert "No such module: http_parser" in caplog.text
    assert not list(tmp_path.glob("appium-*"))


@pytest.mark.anyio
async def test_close_revokes_active_https_stream_immediately(service):
    entered = threading.Event()
    ended = threading.Event()

    async def client():
        try:
            async with service.connect() as raw:
                ctx = ssl.create_default_context(cadata=service.identity.certificate)
                await TLSStream.wrap(raw, hostname="127.0.0.1", ssl_context=ctx, standard_compatible=False)
                entered.set()
                await sleep(60)
        finally:
            ended.set()

    with fail_after(3):
        async with create_task_group() as tasks:
            tasks.start_soon(client)
            while not entered.is_set():
                await sleep(0.01)
            await to_thread.run_sync(service.close)
            while not ended.is_set():
                await sleep(0.01)
    assert not service._connections
    with pytest.raises(RuntimeError):
        async with service.connect():
            pass


@pytest.mark.anyio
async def test_connection_limit(service):
    service._connections = {object(): None for _ in range(128)}
    try:
        with pytest.raises(RuntimeError, match="Too many"):
            async with service.connect():
                pass
    finally:
        service._connections.clear()


@pytest.mark.anyio
async def test_request_queue_preserves_worker_capacity(service, monkeypatch):
    release, started = threading.Event(), threading.Event()

    def request(*args, **kwargs):
        started.set()
        release.wait(3)
        return 200, {"value": None}

    monkeypatch.setattr(service, "request", request)

    class Stream:
        async def receive(self, *args):
            return b"GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n"

        async def send(self, data):
            pass

    with fail_after(5):
        async with create_task_group() as tasks:
            for _ in range(45):
                tasks.start_soon(service.handle, Stream())
            while not started.is_set():
                await sleep(0.01)
            await sleep(0.05)
            assert to_thread.current_default_thread_limiter().borrowed_tokens == 1
            await to_thread.run_sync(service.close)
            release.set()
            tasks.cancel_scope.cancel()


def test_new_session_reserves_fresh_ports(service):
    service.request("POST", "/session", caps())
    first = dict(service.policy.fixed)
    service.request("DELETE", "/session/owned-session", {})
    guards = []
    try:
        for name in ("appium:wdaLocalPort", "appium:mjpegServerPort"):
            guard = module.socket.socket()
            guard.bind(("127.0.0.1", first[name]))
            guards.append(guard)
        service.request("POST", "/session", caps())
        assert service.policy.fixed["appium:wdaLocalPort"] != first["appium:wdaLocalPort"]
        assert service.policy.fixed["appium:mjpegServerPort"] != first["appium:mjpegServerPort"]
    finally:
        for guard in guards:
            guard.close()


@pytest.mark.anyio
async def test_complete_oversized_header_is_rejected(service):
    request = b"GET /status HTTP/1.1\r\nHost: test\r\nX-Oversized: " + b"x" * 20000 + b"\r\n\r\n"
    with fail_after(5):
        status, _ = await exchange(service, request)
    assert status == 400
    assert not (service.directory / "requests.jsonl").exists()


def test_mjpeg_port_reservations(service):
    service.request("POST", "/session", caps())
    port = service.policy.fixed["appium:mjpegServerPort"]
    for family, address in ((module.socket.AF_INET, ("127.0.0.1", port)), (module.socket.AF_INET6, ("::1", port))):
        with module.socket.socket(family, module.socket.SOCK_STREAM) as guard:
            if family == module.socket.AF_INET6:
                guard.setsockopt(module.socket.IPPROTO_IPV6, module.socket.IPV6_V6ONLY, 1)
            with pytest.raises(OSError):
                guard.bind(address)
    service.request("DELETE", "/session/owned-session", {})
    assert not service._reservations


def test_provider_metadata_is_tool_neutral(service):
    result = service.metadata()
    assert set(result) == {"ca_certificate", "metadata"}
    assert result["metadata"] == {
        "protocol": "webdriver",
        "device": {"platform": "iOS", "udid": "OWNED-UDID", "version": "27.0"},
    }


def test_mjpeg_guards_block_reusable_wildcard_bind(service):
    service.request("POST", "/session", caps())
    port = service.policy.fixed["appium:mjpegServerPort"]
    for family, address in ((module.socket.AF_INET, ("0.0.0.0", port)), (module.socket.AF_INET6, ("::", port))):
        with module.socket.socket(family, module.socket.SOCK_STREAM) as attempt:
            attempt.setsockopt(module.socket.SOL_SOCKET, module.socket.SO_REUSEADDR, 1)
            if family == module.socket.AF_INET6:
                attempt.setsockopt(module.socket.IPPROTO_IPV6, module.socket.IPV6_V6ONLY, 1)
            with pytest.raises(OSError):
                attempt.bind(address)
    for family, host in ((module.socket.AF_INET, "127.0.0.1"), (module.socket.AF_INET6, "::1")):
        with module.socket.socket(family, module.socket.SOCK_STREAM) as attempt:
            attempt.settimeout(1)
            # macOS may drop SYNs to reserved sockets instead of refusing them.
            assert attempt.connect_ex((host, port)) in {errno.ECONNREFUSED, errno.EAGAIN, errno.ETIMEDOUT}


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_cleanup_rejects_replaced_state(service, tmp_path, replacement):
    owned = service.directory
    saved = tmp_path / "saved-owned-state"
    unrelated = tmp_path / "unrelated-state"
    unrelated.mkdir()
    marker = unrelated / "keep.txt"
    marker.write_text("unrelated")
    owned.rename(saved)
    if replacement == "directory":
        owned.mkdir()
        replacement_marker = owned / "keep.txt"
        replacement_marker.write_text("replacement")
    else:
        owned.symlink_to(unrelated, target_is_directory=True)
    try:
        with pytest.raises(RuntimeError, match="replaced"):
            service.close()
        assert service.process is None
        assert marker.read_text() == "unrelated"
        if replacement == "directory":
            assert replacement_marker.read_text() == "replacement"
    finally:
        if replacement == "directory":
            replacement_marker.unlink()
            owned.rmdir()
        else:
            owned.unlink()
        saved.rename(owned)
    service.close()
    assert not owned.exists()
