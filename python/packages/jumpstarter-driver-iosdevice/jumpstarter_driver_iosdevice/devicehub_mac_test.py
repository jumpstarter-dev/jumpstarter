import asyncio
import io
import json
import os
import plistlib
import re
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import anyio
import pytest
from anyio import to_thread
from anyio.abc import SocketAttribute

from . import devicehub_mac
from .usbmux import _memory_pair


def _xcode(folder, version, *, device_hub):
    app = folder / f"Xcode-{version}.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
    if device_hub:
        (app / devicehub_mac.DEVICE_HUB_APP).mkdir(parents=True)
    return app


@pytest.fixture
def mac(tmp_path, monkeypatch):
    media = tmp_path / "CoreDeviceMediaStreamSupport.framework"
    media.mkdir()
    monkeypatch.setattr(devicehub_mac, "MEDIA_STREAM_SUPPORT", media)
    apps = []
    monkeypatch.setattr(devicehub_mac, "_xcode_apps", lambda: apps)
    return tmp_path, apps, media


def test_xcode_27_with_device_hub_is_quiet(mac):
    folder, apps, _ = mac
    apps.append(_xcode(folder, "27.1", device_hub=True))
    assert devicehub_mac.xcode_warning() is None


def test_older_xcode_is_named(mac):
    folder, apps, _ = mac
    apps.extend([_xcode(folder, "9.4", device_hub=False), _xcode(folder, "26.4", device_hub=False)])
    assert devicehub_mac.xcode_warning() == (
        "Device Hub needs Xcode 27 or later, but this Mac has Xcode 26.4. "
        "The device will not appear until a newer Xcode is installed."
    )


def test_missing_xcode(mac):
    assert "this Mac has no Xcode" in (devicehub_mac.xcode_warning() or "")


def test_unfinished_install(mac):
    folder, apps, media = mac
    apps.append(_xcode(folder, "27.0", device_hub=True))
    media.rmdir()
    assert "Open Xcode once" in (devicehub_mac.xcode_warning() or "")


def test_xcode_apps_include_selected_and_installed(tmp_path, monkeypatch):
    selected = _xcode(tmp_path / "selected", "27.1", device_hub=True)
    installed = _xcode(tmp_path, "26.4", device_hub=False)

    def run(command, **_kwargs):
        assert command == ["xcode-select", "-p"]
        return type("Result", (), {"stdout": f"{selected}/Contents/Developer\n"})()

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    monkeypatch.setattr(devicehub_mac, "XCODE_FOLDERS", (tmp_path,))
    assert devicehub_mac._xcode_apps() == [selected, installed]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fake_pair(monkeypatch, *, connect_before_code: bool):
    async def pair(client, code, *, timeout):
        if connect_before_code:
            client.connected.set()
        entered = await code()
        # A newly paired Mac connects before pairing returns.
        client.connected.set()
        await anyio.sleep(0.05)
        client.codes.append(entered)

    monkeypatch.setattr(devicehub_mac, "pair", pair)


class _Client:
    def __init__(self):
        self.connected = anyio.Event()
        self.codes = []


@pytest.mark.anyio
async def test_pairing_finishes_when_the_new_pairing_connects(monkeypatch):
    _fake_pair(monkeypatch, connect_before_code=False)
    client = _Client()

    async def code():
        return "123456"

    assert await devicehub_mac._pair_unless_connected(client, code, client.connected) is True
    assert client.codes == ["123456"]


@pytest.mark.anyio
async def test_pairing_abandoned_when_still_paired_mac_connects(monkeypatch):
    _fake_pair(monkeypatch, connect_before_code=True)
    client = _Client()

    async def code():
        await anyio.sleep_forever()

    assert await devicehub_mac._pair_unless_connected(client, code, client.connected) is False
    assert client.codes == []


FAKE_XCODEBUILD = """
import pathlib, signal, sys, time
marker = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
print("Testing started", flush=True)
if mode == "automation":
    print("Failed to initialize for UI testing: Timed out while enabling automation mode.", flush=True)
    sys.exit(65)
if mode == "silent":
    print("something else went wrong", flush=True)
    sys.exit(65)
def stop(*_):
    marker.write_text(" ".join(sys.argv[3:]))
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print("ServerURLHere->http://192.168.1.79:8100<-ServerURLHere", flush=True)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def fake_wda(tmp_path, monkeypatch):
    script = tmp_path / "xcodebuild.py"
    script.write_text(FAKE_XCODEBUILD)
    marker = tmp_path / "stopped"
    record = {
        "hardwareProperties": {"udid": "DEVICE"},
        "connectionProperties": {"tunnelIPAddress": "fd00::1"},
        "deviceProperties": {"osVersionNumber": "27.0.1"},
    }
    monkeypatch.setattr(devicehub_mac, "coredevice", lambda udids: record if "DEVICE" in udids else None)
    monkeypatch.setattr(devicehub_mac, "_wda_ready", lambda url: True)
    monkeypatch.setattr(devicehub_mac, "_connect", lambda udid: None)

    def use(mode):
        monkeypatch.setattr(devicehub_mac, "XCODEBUILD", (sys.executable, str(script), str(marker), mode))

    return use, marker


@pytest.mark.anyio
async def test_webdriveragent_runs_over_the_tunnel_and_stops(fake_wda):
    use, marker = fake_wda
    use("ok")
    xctestrun = Path("WebDriverAgentRunner.xctestrun")
    async with devicehub_mac.webdriveragent(xctestrun, udids=["STAND-IN", "DEVICE"], notify=lambda _: None) as agent:
        # The tunnel address, not the Wi-Fi address WebDriverAgent prints.
        assert agent.url == "http://[fd00::1]:8100"
        assert agent.udid == "DEVICE"
        assert agent.platform_version == "27.0.1"
        assert not marker.exists()
    assert marker.read_text() == "-xctestrun WebDriverAgentRunner.xctestrun -destination id=DEVICE"


@pytest.mark.anyio
async def test_webdriveragent_explains_refused_automation(fake_wda):
    use, _ = fake_wda
    use("automation")
    with pytest.raises(RuntimeError, match="Enable UI Automation"):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            pass


@pytest.mark.anyio
async def test_webdriveragent_reports_xcodebuild_output(fake_wda):
    use, _ = fake_wda
    use("silent")
    with pytest.raises(RuntimeError, match="something else went wrong"):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            pass


@pytest.mark.anyio
async def test_webdriveragent_keeps_caller_errors(fake_wda):
    use, marker = fake_wda
    use("ok")
    with pytest.raises(KeyError):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            raise KeyError("from the test")
    assert marker.exists()


def test_webdriveragent_capabilities_avoid_usbmux():
    agent = devicehub_mac.WebDriverAgent(url="http://[fd00::1]:8100", udid="DEVICE", platform_version="27.0.1")
    capabilities = agent.capabilities()
    assert capabilities["appium:webDriverAgentUrl"] == "http://[fd00::1]:8100"
    assert capabilities["appium:platformVersion"] == "27.0.1"
    assert capabilities["appium:skipLogCapture"] is True
    assert "appium:bundleId" not in capabilities


def test_check_link_needs_a_mac(monkeypatch):
    monkeypatch.setattr(devicehub_mac.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="needs a Mac client"):
        devicehub_mac.check_link()


LINKED = f"feth0: flags\n\tinet6 {devicehub_mac.HOST_ADDRESS} prefixlen 64\n"


@pytest.mark.parametrize(
    "output,bpf,ready",
    [(LINKED, True, True), (LINKED, False, False), ("feth0: flags\n", True, False), (None, True, False)],
)
def test_check_link_explains_the_setup_script(monkeypatch, output, bpf, ready):
    monkeypatch.setattr(devicehub_mac.sys, "platform", "darwin")
    monkeypatch.setattr(devicehub_mac, "_bpf_accessible", lambda: bpf)

    def run(command, **_kwargs):
        assert command == ["ifconfig", devicehub_mac.HOST_INTERFACE]
        if output is None:
            raise devicehub_mac.subprocess.CalledProcessError(1, command)
        return type("Result", (), {"stdout": output})()

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    if ready:
        devicehub_mac.check_link()
    else:
        with pytest.raises(RuntimeError, match=re.escape(f"sudo sh {devicehub_mac.SETUP_SCRIPT}")):
            devicehub_mac.check_link()


def test_bpf_access_needs_every_capture_device(tmp_path):
    assert devicehub_mac._bpf_accessible(tmp_path) is False
    for index in range(2):
        (tmp_path / f"bpf{index}").write_bytes(b"")
    assert devicehub_mac._bpf_accessible(tmp_path) is True
    (tmp_path / "bpf1").chmod(0o400)
    assert devicehub_mac._bpf_accessible(tmp_path) is False


@pytest.fixture
def setup_run(monkeypatch):
    calls, linked = [], []

    def run(command, **_kwargs):
        calls.append(command)
        linked.append(True)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    monkeypatch.setattr(devicehub_mac, "link_ready", lambda: bool(linked))
    return calls, linked


def test_link_is_set_up_with_sudo_once_the_user_agrees(setup_run):
    calls, _ = setup_run
    notes, questions = [], []

    def confirm(question):
        questions.append(question)
        return True

    devicehub_mac.set_up_link(confirm=confirm, notify=notes.append)
    assert calls == [["sudo", "sh", str(devicehub_mac.SETUP_SCRIPT)]]
    assert "once per boot" in notes[0] and "capture packets" in notes[0]
    assert questions == ["Set up the link now with sudo?"]


def test_declined_link_setup_explains_the_manual_step(setup_run):
    calls, _ = setup_run
    with pytest.raises(RuntimeError, match=re.escape(f"sudo sh {devicehub_mac.SETUP_SCRIPT}")):
        devicehub_mac.set_up_link(confirm=lambda question: False, notify=lambda note: None)
    assert calls == []


@pytest.mark.parametrize("failure", ["exit", "missing-sudo", "still-missing"])
def test_failed_link_setup_is_reported(monkeypatch, failure):
    def run(command, **_kwargs):
        if failure == "missing-sudo":
            raise FileNotFoundError("sudo")
        return type("Result", (), {"returncode": 1 if failure == "exit" else 0})()

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    monkeypatch.setattr(devicehub_mac, "link_ready", lambda: False)
    with pytest.raises(RuntimeError, match="Device Hub link"):
        devicehub_mac.set_up_link(confirm=lambda question: True, notify=lambda note: None)


def test_setup_script_is_installed_with_the_package():
    assert devicehub_mac.SETUP_SCRIPT.is_file()
    assert devicehub_mac.SETUP_SCRIPT.read_text().startswith("#!/bin/sh")


def test_txt_record_is_length_prefixed():
    assert devicehub_mac._txt({"ver": 26, "flags": 0}) == b"\x06ver=26\x07flags=0"


def test_xcode_version_ignores_unreadable_bundles(tmp_path):
    assert devicehub_mac._xcode_version(tmp_path / "Missing.app") is None


class FakeDnsSd:
    """libdns_sd's calls, answering browse and resolve requests from Python."""

    def __init__(self, *, errors=(), answers=True):
        self.errors = set(errors)
        self.answers = answers
        self.read, self.write = os.pipe()
        os.write(self.write, b"x")
        self.pending = []
        self.deallocated = 0
        self.registered = []

    def close(self):
        os.close(self.read)
        os.close(self.write)

    def _call(self, name, result=0):
        return 1 if name in self.errors else result

    def DNSServiceCreateConnection(self, _ref):
        return self._call("connection")

    def DNSServiceRegisterRecord(self, *args):
        self.registered.append(("record", args[4]))
        return self._call("record")

    def DNSServiceRegister(self, *args):
        self.registered.append(("service", args[3], args[4], args[9]))
        return self._call("service")

    def DNSServiceRefDeallocate(self, _ref):
        self.deallocated += 1

    def DNSServiceRefSockFD(self, _ref):
        return self.read

    def DNSServiceBrowse(self, _ref, _flags, _index, regtype, _domain, callback, _context):
        assert regtype == devicehub_mac.PAIRABLE_HOST_SERVICE.encode()
        if self.answers:
            self.pending.append(lambda: callback(None, 0x2, 7, 0, b"Mac", regtype, b"local.", None))
        return self._call("browse")

    def DNSServiceResolve(self, _ref, _flags, index, name, _regtype, _domain, callback, _context):
        assert (index, name) == (7, b"Mac")
        self.pending.append(lambda: callback(None, 0, 7, 0, b"Mac", b"mac.local.", socket.htons(51515), 0, None, None))
        return self._call("resolve")

    def DNSServiceProcessResult(self, _ref):
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def dns_sd(monkeypatch):
    libraries = []

    def make(**options):
        library = FakeDnsSd(**options)
        libraries.append(library)
        monkeypatch.setattr(devicehub_mac, "_dns_sd", lambda: library)
        return library

    yield make
    for library in libraries:
        library.close()


@pytest.mark.anyio
async def test_pairable_host_is_browsed_and_resolved(dns_sd):
    library = dns_sd()
    assert await to_thread.run_sync(devicehub_mac.find_pairable_host, 5.0) == 51515
    assert library.deallocated == 2


@pytest.mark.anyio
async def test_pairable_host_search_times_out(dns_sd):
    dns_sd(answers=False)
    with pytest.raises(TimeoutError, match="Pair Nearby Device"):
        await to_thread.run_sync(devicehub_mac.find_pairable_host, 0.05)


@pytest.mark.parametrize("error,message", [("browse", "cannot browse"), ("resolve", "cannot resolve")])
@pytest.mark.anyio
async def test_pairable_host_search_reports_dns_errors(dns_sd, error, message):
    dns_sd(errors=[error])
    with pytest.raises(OSError, match=message):
        await to_thread.run_sync(devicehub_mac.find_pairable_host, 5.0)


def test_advert_publishes_only_on_the_link(dns_sd, monkeypatch):
    library = dns_sd()
    monkeypatch.setattr(devicehub_mac.socket, "if_nametoindex", lambda name: 9)
    advert = devicehub_mac.Advert("ABCDEF12-0000", "tag", devicehub_mac.CONTROL_PORT)
    record, service = library.registered
    assert record == ("record", b"jumpstarter-abcdef12.local.")
    assert service[:3] == ("service", b"ABCDEF12-0000", b"_remotepairing._tcp")
    assert b"authTag=tag" in service[3]
    advert.close()
    advert.close()
    assert library.deallocated == 2


@pytest.mark.parametrize(
    "error,message",
    [("connection", "mDNSResponder"), ("record", "device address"), ("service", "advertise the device")],
)
def test_advert_reports_dns_errors(dns_sd, monkeypatch, error, message):
    dns_sd(errors=[error])
    monkeypatch.setattr(devicehub_mac.socket, "if_nametoindex", lambda name: 9)
    with pytest.raises(OSError, match=message):
        devicehub_mac.Advert("ABCDEF12", "tag", devicehub_mac.CONTROL_PORT)


@pytest.mark.anyio
async def test_relay_carries_both_directions():
    left, left_peer = _memory_pair()
    right, right_peer = _memory_pair()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(devicehub_mac.relay, left, right)
        await left_peer.send(b"to the exporter")
        assert await right_peer.receive() == b"to the exporter"
        await right_peer.send(b"to the Mac")
        assert await left_peer.receive() == b"to the Mac"
        await left_peer.aclose()
        with pytest.raises(anyio.EndOfStream):
            await right_peer.receive()


class FakeClient:
    """The driver client calls Device Hub makes, backed by memory streams."""

    def __init__(self, info=None):
        self.calls = []
        self.info = info or {"name": "iPhone", "identifier": "IDENTIFIER", "auth_tag": "tag", "paired_hosts": 1}
        self.exporter_ends = []
        self.fail_streams = False

    async def call_async(self, method, *args):
        self.calls.append((method, *args))
        if method == "devicehub_info":
            return self.info

    @asynccontextmanager
    async def stream_async(self, method):
        if self.fail_streams:
            raise RuntimeError("lease ended")
        self.calls.append((method,))
        stream, peer = _memory_pair()
        self.exporter_ends.append(peer)
        async with stream:
            yield stream


@pytest.mark.anyio
async def test_mac_connections_are_carried_to_the_exporter():
    client = FakeClient()
    connection, mac = _memory_pair()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(devicehub_mac._carry, client, connection)
        await mac.send(b"RPPairing")
        await anyio.wait_all_tasks_blocked()
        assert await client.exporter_ends[0].receive() == b"RPPairing"
        await mac.aclose()
    assert client.calls == [("connect_devicehub",)]


@pytest.mark.anyio
async def test_failed_mac_connection_is_logged_not_raised():
    client = FakeClient()
    client.fail_streams = True
    connection, _mac = _memory_pair()
    with patch.object(devicehub_mac, "logger") as logger:
        await devicehub_mac._carry(client, connection)
    logger.warning.assert_called_once()
    assert str(logger.warning.call_args.args[1]) == "lease ended"


@pytest.mark.anyio
async def test_pair_relays_device_hub_and_sends_the_code(monkeypatch):
    client = FakeClient()
    received = []

    async def device_hub(stream):
        async with stream:
            received.append(await stream.receive())
            await stream.send(b"pairing reply")

    async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
        port = listener.extra(SocketAttribute.local_port)
        monkeypatch.setattr(devicehub_mac, "find_pairable_host", lambda timeout: port)

        async def code():
            await client.exporter_ends[0].send(b"pairing request")
            return "654321"

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(listener.serve, device_hub)
            with anyio.fail_after(10):
                await devicehub_mac.pair(client, code, timeout=1)
            tasks.cancel_scope.cancel()
    assert received == [b"pairing request"]
    assert await client.exporter_ends[0].receive() == b"pairing reply"
    assert client.calls == [("connect_devicehub_pairing",), ("devicehub_pairing_code", "654321")]


class FakeAdvert:
    made: ClassVar[list["FakeAdvert"]] = []

    def __init__(self, identifier, auth_tag, port):
        self.args = (identifier, auth_tag, port)
        self.closed = False
        FakeAdvert.made.append(self)

    def close(self):
        self.closed = True


@pytest.fixture
def adverts(monkeypatch):
    FakeAdvert.made = []
    monkeypatch.setattr(devicehub_mac, "Advert", FakeAdvert)
    return FakeAdvert.made


@pytest.mark.anyio
async def test_device_is_advertised_afresh_after_pairing(monkeypatch, adverts):
    connected = anyio.Event()
    attempts = []

    async def pair_unless_connected(client, code, event):
        attempts.append(len(adverts))
        if len(attempts) == 2:
            event.set()
        return True

    monkeypatch.setattr(devicehub_mac, "_pair_unless_connected", pair_unless_connected)
    notes = []
    advert = await devicehub_mac._advertise_until_connected(None, None, FakeClient().info, connected, notes.append)
    assert attempts == [1, 2]
    assert adverts[0].closed and advert is adverts[1] and not advert.closed
    assert adverts[0].args == ("IDENTIFIER", "tag", devicehub_mac.CONTROL_PORT)
    assert notes.count("This Mac is paired with iPhone.") == 2


@pytest.mark.anyio
async def test_failed_advertising_withdraws_the_advert(monkeypatch, adverts):
    async def pair_unless_connected(client, code, event):
        raise OSError("mDNSResponder went away")

    monkeypatch.setattr(devicehub_mac, "_pair_unless_connected", pair_unless_connected)
    with pytest.raises(OSError, match="went away"):
        await devicehub_mac._advertise_until_connected(None, None, FakeClient().info, anyio.Event(), print)
    assert adverts[0].closed


class FakeStack:
    def __init__(self, interface, address):
        assert (interface, address) == (devicehub_mac.DEVICE_INTERFACE, devicehub_mac.DEVICE_ADDRESS)
        self.queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def listen(self, port):
        assert port == devicehub_mac.CONTROL_PORT
        return self.queue


@pytest.fixture
def mac_session(monkeypatch, adverts):
    stacks, carried, paired = [], [], []

    def stack(interface, address):
        stacks.append(FakeStack(interface, address))
        return stacks[-1]

    async def carry(client, connection):
        carried.append(connection)

    async def pair(client, code, timeout=300.0):
        paired.append(await code())

    async def pair_unless_connected(client, code, connected):
        stacks[0].queue.put_nowait("connection from the Mac")
        await connected.wait()
        return False

    monkeypatch.setattr(devicehub_mac, "xcode_warning", lambda: "Xcode is old")
    monkeypatch.setattr(devicehub_mac, "check_link", lambda: None)
    monkeypatch.setattr(devicehub_mac, "Stack", stack)
    monkeypatch.setattr(devicehub_mac, "_carry", carry)
    monkeypatch.setattr(devicehub_mac, "pair", pair)
    monkeypatch.setattr(devicehub_mac, "_pair_unless_connected", pair_unless_connected)
    return carried, paired, adverts


async def _code():
    return "111111"


@pytest.mark.anyio
async def test_shown_pairs_a_new_mac_and_carries_its_connections(mac_session):
    carried, paired, adverts = mac_session
    client = FakeClient({**FakeClient().info, "paired_hosts": 0})
    notes = []
    async with devicehub_mac.shown(client, code=_code, force_pair=False, notify=notes.append) as info:
        assert info["name"] == "iPhone"
        await anyio.wait_all_tasks_blocked()
        assert carried == ["connection from the Mac"]
        assert not adverts[0].closed
    assert adverts[0].closed
    assert paired == ["111111"]
    assert notes[0] == "Warning: Xcode is old"
    assert client.calls[0] == ("devicehub_info", devicehub_mac.CONTROL_PORT)


@pytest.mark.anyio
async def test_serve_skips_pairing_for_a_paired_mac_until_cancelled(mac_session):
    carried, paired, adverts = mac_session
    with anyio.move_on_after(0.2):
        await devicehub_mac.serve(FakeClient(), code=_code, force_pair=False, notify=lambda _: None)
    assert paired == [] and carried == ["connection from the Mac"]
    assert adverts[0].closed


@pytest.mark.anyio
async def test_shown_keeps_caller_errors(mac_session):
    with pytest.raises(KeyError):
        async with devicehub_mac.shown(FakeClient(), code=_code, force_pair=True, notify=lambda _: None):
            raise KeyError("from the test")


def test_coredevice_finds_the_device_under_either_udid(monkeypatch):
    devices = [{"hardwareProperties": {"udid": "OTHER"}}, "junk", {"hardwareProperties": {"udid": "DEVICE"}}]

    def run(command, **_kwargs):
        assert command[:4] == ["xcrun", "devicectl", "list", "devices"]
        Path(command[-1]).write_text(json.dumps({"result": {"devices": devices}}))

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    assert devicehub_mac.coredevice(["STAND-IN", "DEVICE"]) == devices[2]
    assert devicehub_mac.coredevice(["MISSING"]) is None


def test_coredevice_is_none_when_devicectl_fails(monkeypatch):
    def run(command, **_kwargs):
        raise devicehub_mac.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    assert devicehub_mac.coredevice(["DEVICE"]) is None


@pytest.mark.parametrize("tunnel,message", [(False, "list the device"), (True, "a tunnel to the device")])
@pytest.mark.anyio
async def test_listing_gives_up_after_the_timeout(monkeypatch, tunnel, message):
    monkeypatch.setattr(devicehub_mac, "coredevice", lambda udids: {"connectionProperties": {}} if tunnel else None)
    with pytest.raises(RuntimeError, match=message):
        await devicehub_mac._listed(["DEVICE"], 0.05, tunnel=tunnel)


def test_wda_ready_reads_the_status_endpoint(monkeypatch):
    def urlopen(url, timeout):
        assert url == "http://[fd00::1]:8100/status"
        return io.BytesIO(json.dumps({"value": {"ready": True}}).encode())

    monkeypatch.setattr(devicehub_mac.urllib.request, "urlopen", urlopen)
    assert devicehub_mac._wda_ready("http://[fd00::1]:8100") is True

    def unreachable(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(devicehub_mac.urllib.request, "urlopen", unreachable)
    assert devicehub_mac._wda_ready("http://[fd00::1]:8100") is False


def test_wda_failure_names_known_causes():
    assert "provisioning profile" in devicehub_mac._wda_failure(["error: ApplicationVerificationFailed"])
    assert devicehub_mac._wda_failure(["a", "b"]).endswith("a\nb")


def test_connect_asks_coredevice_for_the_device(monkeypatch):
    commands = []
    monkeypatch.setattr(devicehub_mac.subprocess, "run", lambda command, **_kwargs: commands.append(command))
    devicehub_mac._connect("DEVICE")
    assert commands[0][:6] == ["xcrun", "devicectl", "device", "info", "details", "--device"]
    assert commands[0][6] == "DEVICE"


@pytest.mark.anyio
async def test_webdriveragent_that_never_answers_times_out(fake_wda, monkeypatch):
    use, marker = fake_wda
    use("ok")
    monkeypatch.setattr(devicehub_mac, "_wda_ready", lambda url: False)
    with pytest.raises(RuntimeError, match="not ready within"):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], timeout=0.5, notify=print):
            pass
    assert marker.exists()


@pytest.mark.anyio
async def test_stop_kills_an_xcodebuild_that_ignores_interrupts(monkeypatch):
    monkeypatch.setattr(devicehub_mac, "WDA_STOP_TIMEOUT", 0.2)
    script = "import signal, sys, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nprint(flush=True)\ntime.sleep(60)"
    async with await anyio.open_process([sys.executable, "-c", script]) as process:
        assert process.stdout is not None
        await process.stdout.receive()
        await devicehub_mac._stop(process)
        assert process.returncode is not None
        await devicehub_mac._stop(process)
