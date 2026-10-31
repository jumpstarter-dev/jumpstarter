import bz2
import json
import lzma
import os
import re
import socket
import subprocess
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import anyio
import pytest
from anyio import to_thread
from click.testing import CliRunner

from .client import _client_url, _echo, _parse
from .driver import IMAGES_VOLUME_MARGIN, Cuttlefish, CvdFlasher, CvdPower
from .health import initialize
from .image_test import CRC32, DONT_CARE, RAW, _android_sparse, _sparse_sample
from jumpstarter.client.core import DriverError
from jumpstarter.client.flasher import FlasherClient
from jumpstarter.common.exceptions import ArgumentError
from jumpstarter.common.resources import PresignedRequestResource
from jumpstarter.common.utils import serve
from jumpstarter.driver import export

BASE = "http://localhost:2080"


@pytest.fixture(autouse=True)
def _mock_adb(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_adb.driver.shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "jumpstarter_driver_adb.driver.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""),
    )


def _op(requests_mock, method, path, op_name="op-1"):
    getattr(requests_mock, method)(f"{BASE}{path}", json={"name": op_name, "done": False})
    requests_mock.post(f"{BASE}/operations/{op_name}/:wait", json={"name": op_name, "done": True})


# --- _parse ---


def test_parse_dict():
    assert _parse('{"a": 1}') == {"a": 1}


def test_parse_list():
    assert _parse("[1, 2]") == [1, 2]


def test_parse_plain():
    assert _parse("text") == "text"


# --- _echo ---


def test_echo_dict(capsys):
    _echo({"k": "v"})
    assert '"k"' in capsys.readouterr().out


def test_echo_list(capsys):
    _echo([1])
    assert "1" in capsys.readouterr().out


def test_echo_str(capsys):
    _echo("hi")
    assert "hi" in capsys.readouterr().out


# --- client methods via serve() ---


def test_list_cvds(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "d1"}]})
    with serve(Cuttlefish()) as client:
        assert client.list_cvds()["cvds"][0]["name"] == "d1"


def test_get_cvd(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"adb_port": 6520})
    with serve(Cuttlefish()) as client:
        assert client.get_cvd()["adb_port"] == 6520


def test_restart_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:restart")
    with serve(Cuttlefish()) as client:
        assert client.restart_cvd()["done"] is True


def test_powerwash_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerwash")
    with serve(Cuttlefish()) as client:
        assert client.powerwash_cvd()["done"] is True


def test_powerbtn_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerbtn")
    with serve(Cuttlefish()) as client:
        assert client.powerbtn_cvd()["done"] is True


def test_status(requests_mock):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok")
    with serve(Cuttlefish()) as client:
        assert client.status() == "OK"


def test_list_operations(requests_mock):
    requests_mock.get(f"{BASE}/operations", json={"operations": []})
    with serve(Cuttlefish()) as client:
        assert client.list_operations()["operations"] == []


def test_non_operation_response(requests_mock):
    """Covers _do_operation returning a non-operation result (no 'done' key)."""
    requests_mock.post(f"{BASE}/cvds/cvd/1/:restart", json={"status": "ready"})
    with serve(Cuttlefish()) as client:
        result = client.restart_cvd()
        assert result["status"] == "ready"


# --- CLI ---


def test_cli_list(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "d1"}]})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["list"])
        assert r.exit_code == 0
        assert "d1" in r.output


def test_cli_get(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"name": "n"})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["get"])
        assert r.exit_code == 0


def test_cli_restart(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:restart")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["restart"])
        assert r.exit_code == 0


def test_cli_powerwash(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerwash")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["powerwash"])
        assert r.exit_code == 0


def test_cli_powerbtn(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerbtn")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["powerbtn"])
        assert r.exit_code == 0


def test_cli_status(requests_mock):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["status"])
        assert r.exit_code == 0
        assert "OK" in r.output


def test_cli_ops(requests_mock):
    requests_mock.get(f"{BASE}/operations", json={"operations": []})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["ops"])
        assert r.exit_code == 0


def test_get_host(requests_mock):
    with serve(Cuttlefish()) as client:
        assert client.get_host() == "localhost"


def test_wait_boot(requests_mock):
    with serve(Cuttlefish(boot_timeout=0)) as client:
        assert client.wait_boot(0) == "OK"


def test_cli_wait_boot(requests_mock):
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["wait-boot", "--timeout", "0"])
        assert r.exit_code == 0


def test_cli_power_on(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "1", "group": "cvd", "status": "Running"}]})
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["power", "on"])
        assert r.exit_code == 0


def test_cli_power_off(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:stop")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["power", "off"])
        assert r.exit_code == 0


def test_cli_power_off_destroy(requests_mock):
    _op(requests_mock, "delete", "/cvds/cvd/1")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["power", "off", "--destroy"])
        assert r.exit_code == 0


def test_cli_power_cycle(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:stop")
    requests_mock.get(
        f"{BASE}/cvds",
        json={"cvds": [{"name": "1", "group": "cvd", "status": "Stopped"}]},
    )
    _op(requests_mock, "post", "/cvds/cvd/1/:start", op_name="op-2")
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["power", "cycle", "--wait", "0"])
        assert r.exit_code == 0


def test_run_with_progress_error():
    from .client import _run_with_progress

    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        _run_with_progress("Testing", boom)


# --- WebRTC display ---


def test_client_url():
    assert _client_url(("127.0.0.1", 8080), "cvd_1-1-1") == "http://127.0.0.1:8080/devices/cvd_1-1-1/files/client.html"


def test_cli_webrtc_prints_url():
    with serve(Cuttlefish(webrtc_url="http://cf:1080")) as client:
        r = CliRunner().invoke(client.cli(), ["webrtc"])
        assert r.exit_code == 0
        assert "http://cf:1080" in r.output


@pytest.mark.parametrize("cvds", [[], [{"webrtc_device_id": "cvd-1-1"}]])
def test_cli_webrtc_forward_requires_provisioned_display(requests_mock, cvds):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"cvds": cvds})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["webrtc", "--forward"])
        assert r.exit_code != 0
        assert "webrtc_turn" in r.output
        assert not requests_mock.called


def test_cli_webrtc_forward_refuses_shared_turn_port(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"cvds": [{"webrtc_device_id": "cvd-1-1"}]})
    # Another jumpstarter forward holds the port with SO_REUSEPORT. Sharing it
    # would split the browser's TURN connections between exporters, so the
    # pinned listener must refuse rather than join.
    with socket.socket() as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        with serve(Cuttlefish(webui_port=2090, turn_port=port)) as client:
            r = CliRunner().invoke(client.cli(), ["webrtc", "--forward"])
    assert r.exit_code != 0
    assert f"cannot bind local TURN port {port}" in r.output


def test_cli_webrtc_forward_refuses_shared_ui_port(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"cvds": [{"webrtc_device_id": "cvd-1-1"}]})
    with socket.socket() as turn_holder:
        turn_holder.bind(("127.0.0.1", 0))
        turn_port = turn_holder.getsockname()[1]
    with socket.socket() as ui_holder:
        ui_holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        ui_holder.bind(("127.0.0.1", 0))
        ui_holder.listen()
        ui_port = ui_holder.getsockname()[1]
        with serve(Cuttlefish(webui_port=2090, turn_port=turn_port)) as client:
            r = CliRunner().invoke(client.cli(), ["webrtc", "--forward", "--ui-port", str(ui_port)])
    assert r.exit_code != 0
    assert f"cannot bind local WebRTC UI port {ui_port}" in r.output


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, CancelledError])
def test_cli_webrtc_forward_cancellation_releases_ports(requests_mock, interrupt):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"cvds": [{"webrtc_device_id": "cvd-1-1"}]})
    with socket.socket() as turn_holder, socket.socket() as ui_holder:
        turn_holder.bind(("127.0.0.1", 0))
        ui_holder.bind(("127.0.0.1", 0))
        turn_port, ui_port = turn_holder.getsockname()[1], ui_holder.getsockname()[1]

    with serve(Cuttlefish(webui_port=2090, turn_port=turn_port)) as client:
        portal_call = client.portal.call

        async def cancel_wait():
            raise anyio.get_cancelled_exc_class()()

        def interrupt_wait(func, *args, **kwargs):
            if func.__name__ == "sleep_forever":
                if interrupt is CancelledError:
                    return portal_call(cancel_wait)
                raise interrupt()
            return portal_call(func, *args, **kwargs)

        with patch.object(client.portal, "call", side_effect=interrupt_wait):
            result = CliRunner().invoke(client.cli(), ["webrtc", "--forward", "--ui-port", str(ui_port)])
        assert result.exit_code == 0, result.exception
    assert "Press Ctrl+C to stop" in result.output
    for port in (turn_port, ui_port):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))


# --- flashing ---

MiB = 1024 * 1024


@pytest.fixture
def images(tmp_path):
    """The directory the CVD loads its images from, as fetched."""
    images = tmp_path / "fetch"
    images.mkdir()
    (images / "boot.img").write_bytes(b"original boot")
    (images / "vbmeta.img").write_bytes(b"original vbmeta")
    return images


def _flashable(images, **config) -> Cuttlefish:
    return Cuttlefish(images_dir=str(images), boot_timeout=0, **config)


def _requested(requests_mock, method, path) -> bool:
    return any(r.method == method and r.path == path for r in requests_mock.request_history)


def _driver_errors(error: BaseException | None):
    """DriverErrors in ``error``, the groups it wraps and its cause/context chain."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, DriverError):
            yield current
        pending.extend(getattr(current, "exceptions", ()))
        pending.extend((current.__cause__, current.__context__))


@contextmanager
def _upload_fails(match: str):
    """Expect a local-file flash to fail with the driver's error ``match``.

    A local file is streamed from a task group, so the driver's error reaches
    the caller wrapped: in an ExceptionGroup, or, when the upload's teardown
    wins the race, as a CancelledError raised while handling it.
    """
    with pytest.raises(BaseException) as raised:
        yield
    assert any(re.search(match, str(error)) for error in _driver_errors(raised.value)), repr(raised.value)


@contextmanager
def _image_server(body: bytes, *, stall_after: int):
    """Serve ``body`` over HTTP, pausing after ``stall_after`` bytes until released."""
    started, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:stall_after])
            self.wfile.flush()
            started.set()
            if release.wait(timeout=30):
                self.wfile.write(body[stall_after:])

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/boot.img", started, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("compress", [None, lzma.compress], ids=["raw", "xz"])
def test_flash_replaces_partition_after_destroying_cvd(requests_mock, images, tmp_path, compress):
    # The CVD has a name the driver did not assign; it must still be the one destroyed.
    requests_mock.get(f"{BASE}/cvds", [
        {"json": {"cvds": [{"group": "cvd", "name": "7", "status": "Running"}]}},
        {"json": {"cvds": []}},
    ])
    _op(requests_mock, "delete", "/cvds/cvd/7", op_name="delete-1")
    _op(requests_mock, "post", "/cvds", op_name="create-1")
    image = b"new kernel" + bytes(32 * MiB) + b"avb footer"
    source = tmp_path / "boot.img"
    source.write_bytes(compress(image) if compress else image)

    with serve(_flashable(images)) as client:
        client.storage.flash(source, target="boot")
        flashed = images / "boot.img"
        assert flashed.read_bytes() == image
        assert flashed.stat().st_blocks * 512 < MiB, "32 MiB of zeros must stay a hole"
        # cvd runs as another user and pads vbmeta images in place when it loads them.
        assert flashed.stat().st_mode & 0o777 == 0o666
        assert not list(images.glob("*.partial"))
        assert _requested(requests_mock, "DELETE", "/cvds/cvd/7")
        assert not _requested(requests_mock, "POST", "/cvds"), "a flash leaves the device off"

        client.power.on()
    assert _requested(requests_mock, "POST", "/cvds")


@pytest.mark.parametrize("targets", [["missing"], ["../vbmeta"], [None], ["boot", "vendor_bot"]])
def test_flash_checks_every_target_before_writing(requests_mock, images, tmp_path, targets):
    source = tmp_path / "x.img"
    source.write_bytes(b"x")
    with serve(_flashable(images)) as client, pytest.raises(ArgumentError, match="this build has: boot, vbmeta"):
        if len(targets) == 1:
            client.storage.flash(source, target=targets[0])
        else:
            client.storage.flash(dict.fromkeys(targets, source))
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not requests_mock.request_history, "the CVD must not be touched for an invalid target"


def test_flash_checks_every_source_before_writing(requests_mock, images, tmp_path):
    source = tmp_path / "boot.img"
    source.write_bytes(b"new boot")
    with serve(_flashable(images)) as client, pytest.raises(ArgumentError, match="no such file: .*missing.img"):
        client.storage.flash({"boot": source, "vbmeta": tmp_path / "missing.img"})
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not requests_mock.request_history, "the CVD must not be touched for a missing file"


def test_driver_rejects_paths_outside_images_dir(requests_mock, images, tmp_path):
    # Skip the client's own check: the driver is the boundary for any caller.
    source = tmp_path / "x.img"
    source.write_bytes(b"x")
    with serve(_flashable(images)) as client, _upload_fails("unknown partition '../vbmeta'"):
        FlasherClient.flash(client.storage, source, target="../vbmeta")
    assert (images / "vbmeta.img").read_bytes() == b"original vbmeta"
    assert not requests_mock.request_history


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_cancelled_flash_releases_the_device(requests_mock, images, anyio_backend):
    """A lease ending mid-flash cancels the exporter's tasks, but a standalone
    exporter's driver lives on into the next lease and must not stay blocked.

    Called on the driver: a client cancelling the call is a one-shot asyncio
    cancellation, which cannot reach this anyio cancel-scope case.
    """
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    _op(requests_mock, "post", "/cvds", op_name="create-1")
    cuttlefish = _flashable(images, flash_idle_timeout=30)
    storage, power = cuttlefish.children["storage"], cuttlefish.children["power"]
    assert isinstance(storage, CvdFlasher) and isinstance(power, CvdPower)
    with _image_server(b"n" * MiB, stall_after=4096) as (url, started, _):
        handle = PresignedRequestResource(headers={}, url=url, method="GET").model_dump(mode="json")
        async with anyio.create_task_group() as tg:
            tg.start_soon(storage.flash, handle, "boot")
            assert await to_thread.run_sync(started.wait, 30)
            tg.cancel_scope.cancel()

    await to_thread.run_sync(power.on)
    assert _requested(requests_mock, "POST", "/cvds")
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not list(images.glob("*.partial"))


def test_cancelled_rpc_holds_the_device_until_its_step_finishes(requests_mock, images):
    """Cancelling the RPC cancels the driver's task directly, which a shield does
    not stop, while the step in progress (destroying the CVD) carries on in a
    worker thread. The flash keeps the device until that step is done, and the
    cancelled call returns with the device free."""
    destroying, release = threading.Event(), threading.Event()

    def inventory():
        destroying.set()
        assert release.wait(30)
        return {"cvds": []}

    _op(requests_mock, "post", "/cvds", op_name="create-1")
    cuttlefish = _flashable(images)
    storage = cuttlefish.children["storage"]
    assert isinstance(storage, CvdFlasher)
    flash, finished, free_on_return = storage.flash, threading.Event(), []

    @export
    async def observed_flash(source, target=None):
        try:
            await flash(source, target)
        finally:
            if target == "boot":  # the flash being cancelled, not the one turned away
                free_on_return.append(cuttlefish._flashing is None)
                finished.set()

    with _image_server(b"new boot", stall_after=8) as (url, _, _), \
            patch.object(cuttlefish._backend, "list_cvds", side_effect=inventory), \
            patch.object(storage, "flash", observed_flash), serve(cuttlefish) as client:
        handle = PresignedRequestResource(headers={}, url=url, method="GET").model_dump(mode="json")
        call = client.storage.portal.start_task_soon(client.storage.call_async, "flash", handle, "boot")
        assert destroying.wait(30)
        call.cancel()
        with pytest.raises(DriverError, match="another flash is in progress"):
            client.storage.flash(url, target="vbmeta")
        release.set()
        assert finished.wait(30)
        assert free_on_return == [True]
        client.power.on()
    assert _requested(requests_mock, "POST", "/cvds")
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not list(images.glob("*.partial"))


def test_failed_download_keeps_the_running_cvd_and_image(requests_mock, images):
    # The new image is staged before the CVD is destroyed, so a source that fails costs neither.
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"group": "cvd", "name": "1", "status": "Running"}]})
    with _image_server(b"n" * MiB, stall_after=4096) as (url, _, _), \
            serve(_flashable(images, flash_idle_timeout=1)) as client, \
            pytest.raises(DriverError, match="no image data received for 1s"):
        client.storage.flash(url, target="boot")
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not list(images.glob("*.partial"))
    assert not _requested(requests_mock, "DELETE", "/cvds/cvd/1")


def test_power_on_refused_while_flashing(requests_mock, images):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    body = b"new boot image" * 4096
    with _image_server(body, stall_after=4096) as (url, started, release), \
            serve(_flashable(images, flash_idle_timeout=30)) as client, ThreadPoolExecutor(1) as pool:
        flashing = pool.submit(client.storage.flash, url, target="boot")
        assert started.wait(timeout=30)
        # A second flash is turned away, and ending it must not release the first.
        with pytest.raises(DriverError, match="another flash is in progress"):
            client.storage.flash(url, target="vbmeta")
        with pytest.raises(DriverError, match="cannot create the CVD while a flash is in progress"):
            client.power.on()
        release.set()
        flashing.result(timeout=30)
    assert (images / "boot.img").read_bytes() == body
    assert not _requested(requests_mock, "POST", "/cvds")


def test_flash_expands_android_sparse_images(requests_mock, images, tmp_path):
    # Android CI ships super.img and userdata.img like this; the CVD reads raw disks.
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    sparse, raw = _sparse_sample(dont_care_blocks=2048)
    source = tmp_path / "super.img.xz"
    source.write_bytes(lzma.compress(sparse))
    with serve(_flashable(images)) as client:
        client.storage.flash(source, target="boot")
    flashed = images / "boot.img"
    assert flashed.read_bytes() == raw
    assert flashed.stat().st_blocks * 512 < MiB, "12 MiB of don't-care and zero fill must stay holes"


def test_flash_keeps_output_a_decompressor_holds_back(requests_mock, images, tmp_path):
    # bz2 returns nothing until it has a whole block, so an empty chunk is not the end.
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    image = os.urandom(MiB)
    source = tmp_path / "boot.img.bz2"
    source.write_bytes(bz2.compress(image))
    with serve(_flashable(images)) as client:
        client.storage.flash(source, target="boot")
    assert (images / "boot.img").read_bytes() == image


@pytest.mark.parametrize("sparse,error", [
    (_android_sparse((RAW, 1, bytes(4096)), major=2), "unsupported Android sparse image: version 2.0"),
    (_android_sparse((0xCAC9, 1, b"")), "unknown Android sparse chunk type 0xcac9"),
    (_android_sparse((RAW, 2, bytes(4096), 12 + 8192)), "Android sparse image ends early"),
    (_android_sparse((DONT_CARE, 3, b""), blocks=5), "has 5 blocks but its chunks cover 3"),
    (_android_sparse((CRC32, 2, bytes(8192))), "type 0xcac4 with 2 blocks is 16 bytes, not 8204"),
    (_android_sparse((CRC32, 2, b"\x12\x34\x56\x78")), "has 2 blocks but its chunks cover 0"),
], ids=["version", "chunk-type", "truncated", "block-count", "chunk-size", "checksum-blocks"])
def test_flash_rejects_corrupt_android_sparse_images(requests_mock, images, tmp_path, sparse, error):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    source = tmp_path / "super.img"
    source.write_bytes(sparse)
    with serve(_flashable(images)) as client, _upload_fails(error):
        client.storage.flash(source, target="boot")
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not list(images.glob("*.partial"))


@pytest.fixture
def boot_failing_runtime(requests_mock):
    """Host Orchestrator whose first create fails the way an unbootable image does.

    The failed ``cvd load`` stays listed as "Boot Failed" under a group the
    runtime chose (cuttlefish-orchestration 1.57); the next create boots.
    """
    inventory: list[dict] = []
    statuses = iter(["Boot Failed", "Running"])

    def create(request, context):
        status = next(statuses)
        inventory.append({"group": "cvd_1", "name": "1", "status": status})
        return {"name": status.replace(" ", "-").lower(), "done": False}

    def delete(request, context):
        inventory.clear()
        return {"name": "deleted", "done": False}

    requests_mock.get(f"{BASE}/cvds", json=lambda request, context: {"cvds": list(inventory)})
    requests_mock.post(f"{BASE}/cvds", json=create)
    requests_mock.delete(f"{BASE}/cvds/cvd_1/1", json=delete)
    requests_mock.post(f"{BASE}/operations/boot-failed/:wait", status_code=500,
                       json={"error": "failed to launch cvd", "details": "assemble_cvd failed"})
    requests_mock.post(f"{BASE}/operations/running/:wait", json=lambda request, context: {"cvds": list(inventory)})
    requests_mock.post(f"{BASE}/operations/deleted/:wait", json={})


def _managed(images, tmp_path) -> Cuttlefish:
    """An exporter as the provisioner configures it, with the probe's health state."""
    runtime_id = tmp_path / "runtime-id"
    runtime_id.write_text("runtime-1")
    initialize(str(tmp_path / "health.json"), str(runtime_id), BASE)
    return _flashable(images, managed=True, env_config={"instances": [{"disk": {"default_build": str(images)}}]},
                      runtime_id_path=str(runtime_id), health_state_path=str(tmp_path / "health.json"))


def _probe_state(tmp_path) -> str:
    return json.loads((tmp_path / "health.json").read_text())["state"]


def test_flashed_images_that_do_not_boot_leave_the_member_usable(boot_failing_runtime, images, tmp_path):
    # The health probe fails the Pod in "failed"; the lessee would lose the lease.
    source = tmp_path / "boot.img"
    source.write_bytes(b"unbootable")
    with serve(_managed(images, tmp_path)) as client:
        client.storage.flash(source, target="boot")
        with pytest.raises(DriverError, match="power on failed after a flash.*\n.*failed to launch cvd"):
            client.power.on()
        assert _probe_state(tmp_path) == "off"
        client.power.on()
    assert _probe_state(tmp_path) == "running"


def test_failed_boot_after_flash_must_leave_no_cvd(boot_failing_runtime, requests_mock, images, tmp_path):
    # A runtime that reports the delete done but still lists the CVD cannot be trusted with another attempt.
    requests_mock.delete(f"{BASE}/cvds/cvd_1/1", json={"name": "deleted", "done": False})
    source = tmp_path / "boot.img"
    source.write_bytes(b"unbootable")
    with serve(_managed(images, tmp_path)) as client:
        client.storage.flash(source, target="boot")
        with pytest.raises(DriverError, match="failed to launch cvd"):
            client.power.on()
    assert _probe_state(tmp_path) == "failed"


def test_managed_flash_destroys_a_cvd_the_driver_did_not_create(requests_mock, images, tmp_path):
    # The runtime names groups itself, and an exporter restarted in the same Pod
    # has not learned the name; the CVD still boots from these images.
    inventory = [{"group": "cvd_1", "name": "1", "status": "Running"}]

    def delete(request, context):
        inventory.clear()
        return {"name": "deleted", "done": False}

    requests_mock.get(f"{BASE}/cvds", json=lambda request, context: {"cvds": list(inventory)})
    requests_mock.delete(f"{BASE}/cvds/cvd_1/1", json=delete)
    requests_mock.post(f"{BASE}/operations/deleted/:wait", json={})
    source = tmp_path / "boot.img"
    source.write_bytes(b"new boot")
    with serve(_managed(images, tmp_path)) as client:
        client.storage.flash(source, target="boot")
    assert _requested(requests_mock, "DELETE", "/cvds/cvd_1/1"), "images replaced under a running CVD"
    assert (images / "boot.img").read_bytes() == b"new boot"


def test_pool_build_that_does_not_boot_still_fails_the_probe(boot_failing_runtime, images, tmp_path):
    with serve(_managed(images, tmp_path)) as client, pytest.raises(DriverError, match="failed to launch cvd"):
        client.power.on()
    assert _probe_state(tmp_path) == "failed"


def test_flash_stops_before_filling_image_volume(requests_mock, images, tmp_path):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    used = sum(path.stat().st_blocks * 512 for path in images.iterdir())
    source = tmp_path / "dense.img"
    source.write_bytes(os.urandom(2 * MiB))
    flashable = _flashable(images, images_volume_limit=used + IMAGES_VOLUME_MARGIN + MiB)
    with serve(flashable) as client, _upload_fails("left in the image volume"):
        client.storage.flash(source, target="boot")
    assert (images / "boot.img").read_bytes() == b"original boot"
    assert not list(images.glob("*.partial"))
