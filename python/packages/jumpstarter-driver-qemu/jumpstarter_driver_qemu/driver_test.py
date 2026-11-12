import asyncio
import json
import logging
import os
import platform
import sys
import tarfile
import threading
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from subprocess import TimeoutExpired
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    Event,
    fail_after,
    move_on_after,
    sleep,
    to_thread,
)
from anyio.lowlevel import checkpoint
from click.testing import CliRunner
from jumpstarter_driver_composite.driver import Composite, Proxy
from jumpstarter_driver_pyserial.client import PySerialClient
from jumpstarter_driver_pyserial.console import Console
from jumpstarter_driver_pyserial.driver import PySerial
from qemu.qmp.protocol import Runstate

from jumpstarter_driver_qemu.driver import Qemu, QemuConsole, QemuFlasher, QemuPower, _QemuSerialStream

from jumpstarter.common.oci import OciCredentials
from jumpstarter.common.utils import serve
from jumpstarter.streams.fanout import ReadOnlyStreamError


@pytest.fixture
def anyio_backend():
    """Use only asyncio backend for anyio tests."""
    return "asyncio"


@pytest.fixture
def qemu_serial(monkeypatch):
    """Exercise real serial PTYs without launching a VM or downloading an image."""
    driver = Qemu()
    power = driver.children["power"]
    console = driver.children["console"]
    assert isinstance(power, QemuPower)
    assert isinstance(console, QemuConsole)
    ptys = []

    def launch(*_args, **_kwargs):
        master, slave = os.openpty()
        os.set_blocking(master, False)
        ptys.append((master, slave))
        process = MagicMock()
        process.poll.return_value = None

        def terminate():
            os.close(master)
            os.close(slave)
            ptys.remove((master, slave))
            process.poll.return_value = 0

        process.terminate.side_effect = terminate
        process.kill.side_effect = terminate
        return process

    async def execute(command):
        if command == "query-chardev":
            return [{"label": "serial0", "filename": f"pty:{os.ttyname(ptys[-1][1])}"}]
        return {}

    qmp = MagicMock()
    qmp.runstate = Runstate.RUNNING
    qmp.execute = AsyncMock(side_effect=execute)
    qmp.disconnect = AsyncMock()
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.Popen", launch)
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.QMPClient", lambda _name: qmp)
    try:
        yield driver, power, console, ptys
    finally:
        power.close()
        for master, slave in ptys:
            os.close(master)
            os.close(slave)
        driver._tmp_dir.cleanup()


async def wait_serial_open(console):
    with fail_after(2):
        while console._transport is None:
            await sleep(0.01)


async def read_pty(master):
    with fail_after(2):
        while True:
            try:
                return os.read(master, 1024)
            except BlockingIOError:
                await sleep(0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["connect", "observe"])
@pytest.mark.parametrize("process_state", ["absent", "exited", "stopped"])
async def test_off_console_is_open_silent_and_does_not_touch_pty(qemu_serial, method, process_state):
    _driver, power, console, _ptys = qemu_serial
    if process_state != "absent":
        await power.on()
        if process_state == "stopped":
            await power.off()
        else:
            power._process.poll.return_value = 1
    fanout = console._get_fanout()
    try:
        with patch.object(PySerial, "_open_source") as physical_open, fail_after(2):
            async with getattr(console, method)() as stream:
                if method == "connect":
                    await stream.send(b"discard while off")
                else:
                    with pytest.raises(ReadOnlyStreamError):
                        await stream.send(b"read-only")
                with move_on_after(0.15) as idle:
                    await stream.receive()
                    raise AssertionError("An unpowered serial line must stay silent")
                assert idle.cancelled_caught
                assert fanout.status()["reader_running"]
                physical_open.assert_not_called()
    finally:
        await fanout.close()


@pytest.mark.anyio
async def test_console_does_not_open_stale_pty_during_startup(qemu_serial, monkeypatch):
    driver, power, console, _ptys = qemu_serial
    fanout = console._get_fanout()
    preparing = Event()
    finish_preparation = Event()
    opened = []
    original_open = PySerial._open_source
    Path(driver._pty).symlink_to("/dev/null")
    driver.validate_partition("root").touch()

    async def image_info(*_args, **_kwargs):
        preparing.set()
        await finish_preparation.wait()
        return SimpleNamespace(check_returncode=lambda: None, stdout=b'{"format": "raw", "virtual-size": 0}')

    @asynccontextmanager
    async def physical_open(self):
        opened.append(self.url)
        async with original_open(self) as stream:
            yield stream

    monkeypatch.setattr("jumpstarter_driver_qemu.driver.run_process", image_info)
    monkeypatch.setattr(PySerial, "_open_source", physical_open)
    startup = asyncio.create_task(power.on())
    try:
        with fail_after(2):
            await preparing.wait()
            async with console.connect() as stream:
                await stream.send(b"discard during startup")
                await sleep(0.15)
                assert not opened
                assert power._serial_process is None
                finish_preparation.set()
                await startup
                await wait_serial_open(console)
                assert opened == [driver._pty]
                assert power._serial_process is power._process
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await fanout.close()


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [RuntimeError, PermissionError, BrokenResourceError, EndOfStream])
async def test_live_vm_serial_failure_stays_visible(qemu_serial, error_type):
    _driver, power, _console, _ptys = qemu_serial
    await power.on()
    error = error_type("unexpected serial failure")

    @asynccontextmanager
    async def factory():
        raise error
        yield  # pragma: no cover

    async with _QemuSerialStream(power=power, source_factory=factory) as stream:
        with pytest.raises(error_type) as exc_info:
            await stream.receive()
        assert exc_info.value is error
        await stream.send_eof()
    with pytest.raises(ClosedResourceError):
        await stream.send(b"closed")
    with pytest.raises(ClosedResourceError):
        await stream.receive()


@pytest.mark.anyio
async def test_power_change_during_port_open_discards_stale_source(qemu_serial):
    _driver, power, _console, _ptys = qemu_serial
    await power.on()
    opening = Event()
    finish_open = Event()
    old_source = AsyncMock()
    old_source.receive.return_value = b"stale output"
    new_source = AsyncMock()
    new_source.receive.return_value = b"current output"
    attempts = 0

    @asynccontextmanager
    async def factory():
        nonlocal attempts
        attempts += 1
        source = old_source if attempts == 1 else new_source
        try:
            if attempts == 1:
                opening.set()
                await finish_open.wait()
            yield source
        finally:
            await source.aclose()

    async with _QemuSerialStream(power=power, source_factory=factory) as stream:
        receiving = asyncio.create_task(stream.receive())
        try:
            with fail_after(2):
                await opening.wait()
                await power.off()
                await power.on()
                finish_open.set()
                assert await receiving == b"current output"
            old_source.receive.assert_not_awaited()
            old_source.aclose.assert_awaited_once()
        finally:
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)
    new_source.aclose.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("restart", [False, True])
async def test_teardown_cannot_publish_stale_serial_readiness(qemu_serial, monkeypatch, restart):
    _driver, power, _console, ptys = qemu_serial
    disconnecting = Event()
    qmp = MagicMock()
    qmp.runstate = Runstate.RUNNING

    async def execute(command):
        if command == "query-chardev":
            return [{"label": "serial0", "filename": f"pty:{os.ttyname(ptys[-1][1])}"}]
        return {}

    async def disconnect():
        if not disconnecting.is_set():
            disconnecting.set()
            await Event().wait()

    qmp.execute = AsyncMock(side_effect=execute)
    qmp.disconnect = AsyncMock(side_effect=disconnect)
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.QMPClient", lambda _name: qmp)
    startup = asyncio.create_task(power.on())
    try:
        with fail_after(2):
            await disconnecting.wait()
            power.close()
            await startup
            assert power._serial_process is None
            if restart:
                await power.on()
                assert power._serial_process is power._process
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["off", "console_close"])
@pytest.mark.parametrize("startup_fails", [False, True])
async def test_power_off_waits_for_startup(qemu_serial, monkeypatch, method, startup_fails):
    _driver, power, console, _ptys = qemu_serial
    starting = Event()
    finish_start = Event()
    qmp = MagicMock()
    qmp.runstate = Runstate.RUNNING
    qmp.execute = AsyncMock(return_value=[{"label": "serial0", "filename": "pty:/dev/null"}])

    async def disconnect():
        starting.set()
        await finish_start.wait()
        if startup_fails:
            raise RuntimeError("startup failed")

    qmp.disconnect = AsyncMock(side_effect=disconnect)
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.QMPClient", lambda _name: qmp)
    startup = asyncio.create_task(power.on())
    stopping = None
    try:
        with fail_after(3):
            await starting.wait()
            process = power._process
            stopping = asyncio.create_task(
                power.off() if method == "off" else to_thread.run_sync(console.close)
            )
            await sleep(0.05)
            assert not stopping.done()
            process.terminate.assert_not_called()
            assert power._serial_process is None
            finish_start.set()
            if startup_fails:
                with pytest.raises(RuntimeError, match="startup failed"):
                    await startup
            else:
                await startup
            await stopping
            process.terminate.assert_called_once_with()
            assert power._serial_process is None
            assert not hasattr(power, "_process")
    finally:
        finish_start.set()
        startup.cancel()
        if stopping is not None:
            await asyncio.gather(startup, stopping, return_exceptions=True)
        else:
            await asyncio.gather(startup, return_exceptions=True)


@pytest.mark.anyio
async def test_teardown_cancels_startup_before_launch(qemu_serial, monkeypatch):
    driver, power, _console, ptys = qemu_serial
    driver.validate_partition("root").touch()
    preparing = Event()

    async def image_info(*_args, **_kwargs):
        preparing.set()
        await Event().wait()

    monkeypatch.setattr("jumpstarter_driver_qemu.driver.run_process", image_info)
    startup = asyncio.create_task(power.on())
    try:
        with fail_after(2):
            await preparing.wait()
            power.close()
            await startup
            assert not ptys
            assert not hasattr(power, "_process")
            assert power._serial_process is None
    finally:
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)


@pytest.mark.anyio
async def test_shutdown_interrupts_pending_read_without_blocking_loop(qemu_serial):
    _driver, power, _console, _ptys = qemu_serial
    await power.on()
    process = power._process
    reading = Event()
    disconnected = Event()
    waiting = threading.Event()
    finish_wait = threading.Event()

    async def read():
        reading.set()
        await Event().wait()

    @asynccontextmanager
    async def factory():
        try:
            yield SimpleNamespace(receive=read)
        finally:
            disconnected.set()

    def wait(*_args, **_kwargs):
        waiting.set()
        assert finish_wait.wait(3), "process wait blocked the event loop"

    process.wait.side_effect = wait
    stopping = restarting = None
    async with _QemuSerialStream(power=power, source_factory=factory) as stream:
        receiving = asyncio.create_task(stream.receive())
        try:
            with fail_after(2):
                await reading.wait()
                stopping = asyncio.create_task(power.off())
                assert await to_thread.run_sync(waiting.wait, 1)
                await disconnected.wait()
                assert power._serial_process is None
                assert not stopping.done()
                restarting = asyncio.create_task(power.on())
                await checkpoint()
                assert power._process is process
                finish_wait.set()
                await stopping
                await restarting
                assert power._process is not process
        finally:
            finish_wait.set()
            receiving.cancel()
            await asyncio.gather(
                receiving, *[task for task in (stopping, restarting) if task is not None], return_exceptions=True
            )


@pytest.mark.anyio
@pytest.mark.parametrize("powered_on", [False, True])
async def test_console_waits_for_notification_and_close_wakes_reader(qemu_serial, powered_on):
    _driver, power, _console, _ptys = qemu_serial
    if powered_on:
        await power.on()
    changed = power._serial_changed
    changed.wait = AsyncMock(wraps=changed.wait)
    reading = Event()

    async def read():
        reading.set()
        await Event().wait()

    @asynccontextmanager
    async def factory():
        assert powered_on, "An off console must not open a PTY"
        yield SimpleNamespace(receive=read)

    async with _QemuSerialStream(power=power, source_factory=factory) as stream:
        receiving = asyncio.create_task(stream.receive())
        try:
            if powered_on:
                with fail_after(1):
                    await reading.wait()
            else:
                await sleep(0.15)
                changed.wait.assert_awaited_once()
            assert not receiving.done()
            await stream.aclose()
            with fail_after(1), pytest.raises(ClosedResourceError):
                await receiving
        finally:
            receiving.cancel()
            await asyncio.gather(receiving, return_exceptions=True)


@pytest.mark.anyio
async def test_serial_shutdown_releases_physical_port(qemu_serial):
    _driver, power, console, _ptys = qemu_serial
    await power.on()
    fanout = console._get_fanout()
    try:
        async with console.connect():
            await wait_serial_open(console)
            serial_port = console._transport.serial
            assert serial_port.is_open
            console.shutdown()
            with fail_after(2):
                while serial_port.is_open:
                    await sleep(0.01)
    finally:
        await fanout.close()


@pytest.mark.anyio
@pytest.mark.parametrize("returncode", [None, 0, 1, -15])
async def test_power_on_does_not_replace_process_before_power_off(monkeypatch, returncode):
    driver = Qemu()
    power = driver.children["power"]
    assert isinstance(power, QemuPower)
    process = MagicMock()
    process.poll.return_value = returncode
    power._process = process
    power._cidata = TemporaryDirectory(dir=driver._tmp_dir.name)
    cidata_path = Path(power._cidata.name)
    launch = MagicMock(side_effect=AssertionError("An existing process must not be replaced"))
    monkeypatch.setattr("jumpstarter_driver_qemu.driver.Popen", launch)
    try:
        await power.on()

        # Keep the pre-existing guard even after process exit: only off() clears
        # the saved process and its resources before the next startup.
        launch.assert_not_called()
        assert power._process is process
        assert cidata_path.exists()
    finally:
        driver._tmp_dir.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize("process_state", ["absent", 0, 1, -15])
async def test_power_off_recognizes_already_stopped_vm(caplog, process_state):
    driver = Qemu()
    power = driver.children["power"]
    assert isinstance(power, QemuPower)
    caplog.set_level(logging.WARNING, logger=power.logger.name)
    process = MagicMock()
    if process_state != "absent":
        process.poll.return_value = process_state
        power._process = process
    power._cidata = TemporaryDirectory(dir=driver._tmp_dir.name)
    try:
        await power.off()

        assert "already powered off" in caplog.text
        assert not hasattr(power, "_process")
        assert not hasattr(power, "_cidata")
        process.terminate.assert_not_called()
        process.wait.assert_not_called()
        process.kill.assert_not_called()

        caplog.clear()
        await power.off()
        assert "already powered off" in caplog.text
    finally:
        driver._tmp_dir.cleanup()


@pytest.mark.anyio
@pytest.mark.parametrize("needs_kill", [False, True])
async def test_power_off_stops_live_vm(caplog, needs_kill):
    driver = Qemu()
    power = driver.children["power"]
    assert isinstance(power, QemuPower)
    caplog.set_level(logging.WARNING, logger=power.logger.name)
    process = MagicMock()
    process.poll.return_value = None
    if needs_kill:
        process.wait.side_effect = TimeoutExpired("qemu", 5)
    power._process = process
    power._cidata = TemporaryDirectory(dir=driver._tmp_dir.name)
    try:
        await power.off()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        assert process.kill.call_count == int(needs_kill)
        assert "already powered off" not in caplog.text
        assert not hasattr(power, "_process")
        assert not hasattr(power, "_cidata")

        await power.off()
        assert "already powered off" in caplog.text
        process.terminate.assert_called_once_with()
    finally:
        driver._tmp_dir.cleanup()


@pytest.mark.parametrize("process_state", ["absent", "exited", "running"])
def test_qemu_teardown_is_quiet_and_idempotent(caplog, process_state):
    driver = Qemu()
    power = driver.children["power"]
    assert isinstance(power, QemuPower)
    caplog.set_level(logging.DEBUG, logger=power.logger.name)
    process = MagicMock()
    if process_state != "absent":
        process.poll.return_value = None if process_state == "running" else 1
        power._process = process
    power._cidata = TemporaryDirectory(dir=driver._tmp_dir.name)
    try:
        # The power child is shared with the console, so a root close reaches it
        # twice. Repeating teardown must remain safe and quiet as well.
        driver.close()
        driver.close()

        records = [
            (record.levelno, record.getMessage())
            for record in caplog.records
            if record.name in ("driver.QemuPower", power.logger.name)
        ]
        assert records
        assert set(records) == {(logging.DEBUG, "already powered off, ignoring request")}
        assert not hasattr(power, "_process")
        assert not hasattr(power, "_cidata")
        if process_state == "running":
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5)
        else:
            process.terminate.assert_not_called()
            process.wait.assert_not_called()
        process.kill.assert_not_called()
    finally:
        driver._tmp_dir.cleanup()


@pytest.mark.anyio
async def test_console_and_observer_survive_power_cycles_without_reconnecting(qemu_serial, caplog):
    _driver, power, console, ptys = qemu_serial
    caplog.set_level(logging.WARNING)
    fanout = console._get_fanout()
    try:
        with fail_after(5):
            async with console.connect() as stream, console.observe() as observer:
                logical_source = fanout._source
                reader = fanout._reader_task
                holder = fanout._write_token_holder
                await stream.send(b"discard before first boot")
                for _ in range(2):
                    await power.on()
                    await wait_serial_open(console)
                    master = ptys[-1][0]
                    with pytest.raises(BlockingIOError):
                        os.read(master, 1024)  # Offline input must never be replayed.
                    os.write(master, b"boot output\n")
                    assert await stream.receive() == b"boot output\n"
                    assert await observer.receive() == b"boot output\n"
                    await stream.send(b"typed while running")
                    assert await read_pty(master) == b"typed while running"
                    await power.off()
                    await stream.send(b"discard while off")
                    while console._transport is not None:
                        await sleep(0.01)
                    assert fanout._source is logical_source
                    assert fanout._reader_task is reader
                    assert fanout._write_token_holder == holder
                assert "Source disconnected" not in caplog.text
    finally:
        await fanout.close()


@pytest.mark.anyio
async def test_write_racing_with_power_off_does_not_close_console(qemu_serial, monkeypatch, caplog):
    _driver, power, console, _ptys = qemu_serial
    caplog.set_level(logging.WARNING)
    fanout = console._get_fanout()
    try:
        async with console.connect() as stream:
            await power.on()
            await wait_serial_open(console)
            logical_source = fanout._source
            assert isinstance(logical_source, _QemuSerialStream)

            async def disconnect_on_write(_data):
                await power.off()
                raise BrokenResourceError

            assert logical_source._source is not None
            monkeypatch.setattr(logical_source._source, "send", disconnect_on_write)
            await stream.send(b"typed at disconnect")
            with fail_after(2):
                while console._transport is not None:
                    await sleep(0.01)
            assert fanout._source is logical_source
            assert fanout.status()["write_token_held"]
            assert "Source disconnected" not in caplog.text
    finally:
        await fanout.close()


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [RuntimeError, PermissionError, EndOfStream])
async def test_live_vm_write_failure_stays_visible(qemu_serial, monkeypatch, error_type):
    _driver, power, console, _ptys = qemu_serial
    fanout = console._get_fanout()
    try:
        await power.on()
        async with console.connect() as stream:
            await wait_serial_open(console)
            logical_source = fanout._source
            assert isinstance(logical_source, _QemuSerialStream)
            assert logical_source._source is not None
            error = error_type("unexpected write failure")
            monkeypatch.setattr(logical_source._source, "send", AsyncMock(side_effect=error))
            with pytest.raises(error_type) as exc_info:
                await stream.send(b"input")
            assert exc_info.value is error
    finally:
        await fanout.close()


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [BrokenResourceError, ClosedResourceError])
async def test_transport_write_failure_uses_fanout_discard_policy(qemu_serial, monkeypatch, caplog, error_type):
    _driver, power, console, _ptys = qemu_serial
    caplog.set_level(logging.WARNING)
    await power.on()
    fanout = console._get_fanout()
    try:
        async with console.connect() as stream:
            await wait_serial_open(console)
            logical_source = fanout._source
            assert isinstance(logical_source, _QemuSerialStream)
            assert logical_source._source is not None
            monkeypatch.setattr(logical_source._source, "send", AsyncMock(side_effect=error_type))
            await stream.send(b"failed input")
            await stream.send(b"more failed input")
            assert fanout._source is logical_source
            assert fanout.status()["write_token_held"]
            assert caplog.text.count("Discarding input until source reconnects") == 1
    finally:
        await fanout.close()


def test_client_stream_survives_offline_input_and_power_cycles(qemu_serial):
    driver, _power, console, ptys = qemu_serial
    with serve(driver) as client:
        async def exercise():
            with fail_after(5):
                async with client.console.stream_async("connect") as stream:
                    for _ in range(2):
                        await stream.send(b"discard while off")
                        await sleep(0.15)
                        await client.power.call_async("on")
                        await wait_serial_open(console)
                        master = ptys[-1][0]
                        with pytest.raises(BlockingIOError):
                            os.read(master, 1024)
                        os.write(master, b"boot\n")
                        assert await stream.receive() == b"boot\n"
                        await stream.send(b"working input")
                        assert await read_pty(master) == b"working input"
                        await client.power.call_async("off")

        client.portal.call(exercise)


@pytest.mark.parametrize("route", ["nested", "reference"])
@pytest.mark.parametrize("observe", [False, True])
def test_cli_opens_off_console_with_hotkeys_and_references(qemu_serial, monkeypatch, route, observe):
    driver, power, _console, _ptys = qemu_serial
    root = Composite(
        children={"qemu": driver, "power": Proxy(ref="qemu.power"), "console": Proxy(ref="qemu.console")}
    )
    original_run = Console._Console__run  # ty: ignore[unresolved-attribute]

    async def bounded_run(self):
        with fail_after(5):
            await original_run(self)

    monkeypatch.setattr(Console, "_Console__run", bounded_run)
    with serve(root) as client:
        qemu = client.children["qemu"]
        console_client = qemu.children["console"] if route == "nested" else client.children["console"]
        assert isinstance(console_client, PySerialClient)
        assert console_client.uuid == qemu.children["console"].uuid
        commands = ["qemu", "console", "console"] if route == "nested" else ["console", "console"]
        if observe:
            commands.append("--observe")
        keys = ("" if observe else "\x1d" * 3) + "\x02" * 3
        with patch.object(Console, "setraw", return_value=nullcontext()):
            result = CliRunner().invoke(client.cli(), commands, input=keys)
        assert result.exit_code == 0, result.exception
        assert (power._serial_process is None) == observe


@pytest.fixture(scope="session")
def ovmf(tmpdir_factory):
    tmp_path = tmpdir_factory.mktemp("ovmf")

    # renovate: datasource=github-releases depName=rust-osdev/ovmf-prebuilt
    ver = "edk2-stable202605-r1"
    url = f"https://github.com/rust-osdev/ovmf-prebuilt/releases/download/{ver}/{ver}-bin.tar.xz"

    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with (tmp_path / "ovmf.tar.xz").open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    with tarfile.open(tmp_path / "ovmf.tar.xz") as tf:
        tf.extractall(tmp_path, filter="data")

    yield tmp_path / f"{ver}-bin"


# Get native architecture
def get_native_arch_config():
    native_arch = platform.machine()
    if native_arch == "x86_64":
        return "x86_64", "x64"
    elif native_arch == "aarch64":
        return "aarch64", "aarch64"
    else:
        pytest.skip(f"Unsupported architecture: {native_arch}")  # ty: ignore[too-many-positional-arguments]


@pytest.mark.xfail(
    platform.system() == "Darwin" and os.getenv("GITHUB_ACTIONS") == "true",
    reason="QEMU tests are flaky on macOS in GitHub CI",
)
def test_driver_qemu(tmp_path, ovmf):
    arch, ovmf_arch = get_native_arch_config()

    with serve(
        Qemu(
            arch=arch,
            default_partitions={
                "OVMF_CODE.fd": ovmf / ovmf_arch / "code.fd",
                "OVMF_VARS.fd": ovmf / ovmf_arch / "vars.fd",
            },
        )
    ) as qemu:
        hostname = qemu.hostname
        username = qemu.username
        password = qemu.password

        cached_image = Path(__file__).parent.parent / "images" / f"Fedora-Cloud-Base-Generic-43-1.6.{arch}.qcow2"

        if cached_image.exists():
            qemu.flasher.flash(cached_image.resolve())
        else:
            qemu.flasher.flash(
                f"https://download.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/{arch}/images/Fedora-Cloud-Base-Generic-43-1.6.{arch}.qcow2",
            )

        qemu.power.on()

        with qemu.novnc() as _:
            pass

        with qemu.console.pexpect() as p:
            p.logfile = sys.stdout.buffer
            p.expect_exact(f"{hostname} login:", timeout=600)
            p.sendline(username)
            p.expect_exact("Password:")
            p.sendline(password)
            p.expect_exact(f"[{username}@{hostname} ~]$")
            p.sendline("sudo setenforce 0")
            p.expect_exact(f"[{username}@{hostname} ~]$")

        with qemu.shell() as s:
            assert s.run("uname -r").stdout.strip() == f"6.17.1-300.fc43.{arch}"

        qemu.power.off()


@pytest.fixture
def resize_test():
    """Create a Qemu driver with a sparse root disk, cleanup after test."""
    driver = None

    def _create(disk_size, current_size_gb):
        nonlocal driver
        driver = Qemu(disk_size=disk_size)
        root = Path(driver._tmp_dir.name) / "root"
        root.write_bytes(b"")
        os.truncate(root, current_size_gb * 1024**3)
        return driver, current_size_gb * 1024**3

    yield _create

    if driver:
        driver._tmp_dir.cleanup()


def _mock_qemu_img_info(virtual_size):
    """Return a mock for run_process that simulates qemu-img info."""

    async def mock(cmd, **kwargs):
        result = AsyncMock()
        result.returncode = 0
        result.stdout = json.dumps({"format": "raw", "virtual-size": virtual_size}).encode()
        result.check_returncode = lambda: None
        return result

    return mock


@pytest.mark.anyio
async def test_resize_shrink_blocked(resize_test):
    """Shrinking disk should raise RuntimeError."""
    driver, current = resize_test("10G", 20)  # requested: 10G, current: 20G

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)),
        pytest.raises(RuntimeError, match="Shrinking disk is not supported"),
    ):
        await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_insufficient_space_blocked(resize_test):
    """Resize beyond available host space should raise RuntimeError."""
    driver, current = resize_test("100G", 10)  # requested: 100G, current: 10G

    mock_usage = SimpleNamespace(free=5 * 1024**3)  # only 5G free

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)),
        patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage),
        pytest.raises(RuntimeError, match="Not enough disk space"),
    ):
        await driver.children["power"].on()


@pytest.mark.anyio
async def test_resize_succeeds(resize_test):
    """Resize should call qemu-img resize with correct size."""
    driver, current = resize_test("20G", 10)  # requested: 20G, current: 10G
    mock_usage = SimpleNamespace(free=50 * 1024**3)

    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(current)) as mock_run,
        patch("jumpstarter_driver_qemu.driver.shutil.disk_usage", return_value=mock_usage),
        patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")),
        pytest.raises(RuntimeError, match="mock popen"),
    ):
        await driver.children["power"].on()

    # Find the resize call and verify size argument
    resize_calls = [c for c in mock_run.call_args_list if "resize" in c.args[0]]
    assert resize_calls, "qemu-img resize should be called"
    resize_cmd = resize_calls[0].args[0]  # ['qemu-img', 'resize', path, size]
    assert resize_cmd[-1] == str(20 * 1024**3)


@pytest.fixture
def cmdline_test():
    """Create a Qemu driver with a root disk, cleanup after test."""
    driver = None

    def _create(**kwargs):
        nonlocal driver
        driver = Qemu(**kwargs)
        root = Path(driver._tmp_dir.name) / "root"
        root.write_bytes(b"")
        return driver

    yield _create

    if driver:
        driver._tmp_dir.cleanup()


async def _captured_cmdline(driver):
    """Power on `driver`, aborting at Popen, and return the cmdline it was given."""
    with (
        patch("jumpstarter_driver_qemu.driver.run_process", side_effect=_mock_qemu_img_info(0)),
        patch("jumpstarter_driver_qemu.driver.Popen", side_effect=RuntimeError("mock popen")) as mock_popen,
        pytest.raises(RuntimeError, match="mock popen"),
    ):
        await driver.children["power"].on()

    return mock_popen.call_args.args[0]


@pytest.mark.anyio
async def test_virtio_transport_defaults_to_pci(cmdline_test):
    """Default transport should keep the existing PCI-attached devices."""
    cmdline = await _captured_cmdline(cmdline_test())

    assert "virtio-net-pci,netdev=eth0" in cmdline
    assert "virtio-blk-pci,drive=rootfs,bootindex=1" in cmdline
    assert "virtio-blk-pci,drive=cidata" in cmdline


@pytest.mark.anyio
async def test_virtio_transport_mmio(cmdline_test):
    """mmio transport should switch network and disk devices, for firmware
    (e.g. u-boot booting an aboot image) that can't enumerate PCI early enough
    to see them."""
    # mmio images need >=4G (their DTB hardcodes it); see below.
    cmdline = await _captured_cmdline(cmdline_test(virtio_transport="mmio", mem="4G"))

    assert "virtio-net-device,netdev=eth0" in cmdline
    assert "virtio-blk-device,drive=rootfs,bootindex=1" in cmdline
    # No cidata disk in mmio mode: u-boot's aboot bootcmd probes a single virtio
    # device (devnum=0), and QEMU's reverse virtio-mmio ordering would otherwise
    # let the GPT-less cidata disk shadow the real boot disk. Matches air --aboot.
    assert not any("cidata" in str(arg) for arg in cmdline)
    assert "virtio-net-pci,netdev=eth0" not in cmdline
    assert "virtio-blk-pci,drive=rootfs,bootindex=1" not in cmdline


@pytest.mark.anyio
async def test_virtio_transport_mmio_requires_4g(cmdline_test):
    """mmio boots AIB aboot images whose DTB hardcodes a 4G memory layout, so
    too-little memory must fail loudly instead of silently failing to boot."""
    driver = cmdline_test(virtio_transport="mmio")  # default mem=512M

    with patch("jumpstarter_driver_qemu.driver.Popen") as mock_popen, pytest.raises(RuntimeError, match="4G"):
        await driver.children["power"].on()

    mock_popen.assert_not_called()


def test_set_disk_size_valid():
    """Valid size strings should be accepted."""
    driver = Qemu()
    driver.set_disk_size("20G")
    assert driver.disk_size == "20G"


def test_set_disk_size_invalid():
    """Invalid size strings should raise ValueError."""
    driver = Qemu()
    with pytest.raises(ValueError, match="Invalid size"):
        driver.set_disk_size("invalid")


def test_set_memory_size_valid():
    """Valid size strings should be accepted."""
    driver = Qemu()
    driver.set_memory_size("2G")
    assert driver.mem == "2G"


def test_set_memory_size_invalid():
    """Invalid size strings should raise ValueError."""
    driver = Qemu()
    with pytest.raises(ValueError, match="Invalid size"):
        driver.set_memory_size("invalid")


def test_disk_dir_uses_tmp_by_default():
    driver = Qemu()
    assert driver._disk_dir == driver._tmp_dir.name


def test_disk_dir_stays_with_shared_in_tests(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    driver = Qemu(launcher_socket=str(shared / "launcher.sock"))
    assert driver._disk_dir == str(shared)


def test_disk_dir_is_slash_disk_in_production_sidecar():
    driver = Qemu(launcher_socket="/shared/launcher.sock")
    assert driver._disk_dir == "/disk"


def test_console_wires_power_child():
    """The console gets the power child so its hotkey can power-cycle the VM."""
    driver = Qemu()
    assert driver.children["console"].children["power"] is driver.children["power"]


def test_cidata_uses_tmp_by_default():
    """Local mode keeps cloud-init vvfat content under a system temp dir."""
    driver = Qemu()
    cidata = driver.cidata()
    try:
        assert Path(cidata.name).exists()
        assert (Path(cidata.name) / "meta-data").is_file()
        assert (Path(cidata.name) / "user-data").is_file()
        assert not str(cidata.name).startswith("/shared")
    finally:
        cidata.cleanup()


def test_cidata_uses_shared_work_dir_with_launcher_socket(tmp_path):
    """Sidecar mode must place cidata on the shared volume so QEMU can read it."""
    shared = tmp_path / "shared"
    shared.mkdir()
    # Real _work_dir derives from the launcher socket parent directory.
    driver = Qemu(launcher_socket=str(shared / "launcher.sock"))
    cidata = driver.cidata()
    try:
        assert Path(cidata.name).is_relative_to(shared)
        assert (Path(cidata.name) / "meta-data").is_file()
        assert (Path(cidata.name) / "user-data").is_file()
    finally:
        cidata.cleanup()


# OCI Flash Tests


def _create_mock_process(stdout_lines=None, stderr_lines=None, returncode=0):
    """Create a mock asyncio subprocess process for testing flash_oci."""
    if stdout_lines is None:
        stdout_lines = []
    if stderr_lines is None:
        stderr_lines = []

    process = MagicMock()
    process.returncode = returncode
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()

    stdout_data = [line.encode() if isinstance(line, str) else line for line in stdout_lines] + [b""]
    stdout_stream = MagicMock()
    stdout_stream.readline = AsyncMock(side_effect=stdout_data)
    process.stdout = stdout_stream

    stderr_data = [line.encode() if isinstance(line, str) else line for line in stderr_lines] + [b""]
    stderr_stream = MagicMock()
    stderr_stream.readline = AsyncMock(side_effect=stderr_data)
    process.stderr = stderr_stream

    return process


async def _collect_flash_oci(flasher, *args, **kwargs):
    """Collect all output from flash_oci async generator."""
    results = []
    async for chunk in flasher.flash_oci(*args, **kwargs):
        results.append(chunk)
    return results


@pytest.mark.anyio
async def test_flash_oci_success():
    """flash_oci should invoke fls from-url with the correct arguments."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    expected_target = str(Path(driver._tmp_dir.name) / "root")
    mock_process = _create_mock_process(stdout_lines=["Flashing complete\n"])

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="/usr/local/bin/fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process) as mock_exec,
    ):
        results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

        # Verify final chunk has returncode 0
        assert any(r[2] == 0 for r in results)

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args.args[0] == "/usr/local/bin/fls"
        assert call_args.args[1] == "from-url"
        assert call_args.args[2] == "oci://quay.io/org/image:tag"
        assert call_args.args[3] == expected_target


@pytest.mark.anyio
async def test_flash_oci_with_partition():
    """flash_oci should write to the correct partition path."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    expected_target = str(Path(driver._tmp_dir.name) / "bios")
    mock_process = _create_mock_process()

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process) as mock_exec,
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/org/bios:v1", partition="bios")

        assert mock_exec.call_args.args[3] == expected_target


@pytest.mark.anyio
async def test_flash_oci_with_credentials():
    """OCI credentials should be passed via env vars, not command args."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process) as mock_exec,
    ):
        await _collect_flash_oci(
            flasher,
            "oci://quay.io/private/image:tag",
            oci_username="myuser",
            oci_password="mypass",
        )

        # Credentials should NOT appear in command args
        assert "myuser" not in mock_exec.call_args.args
        assert "mypass" not in mock_exec.call_args.args

        # Credentials should be in env vars
        env = mock_exec.call_args.kwargs["env"]
        assert env["FLS_REGISTRY_USERNAME"] == "myuser"
        assert env["FLS_REGISTRY_PASSWORD"] == "mypass"


@pytest.mark.anyio
async def test_flash_oci_no_credentials():
    """Without credentials, env should be None (inherit parent env)."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    # Ensure OCI env vars are not set so driver doesn't pick them up
    env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
    with (
        patch.dict(os.environ, env_clean, clear=True),
        patch("jumpstarter.common.oci.read_auth_file_credentials", return_value=OciCredentials()),
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),patch(
        "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process
    ) as mock_exec
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/public/image:tag")

        env = mock_exec.call_args.kwargs["env"]
        assert env is None


@pytest.mark.anyio
async def test_flash_oci_credentials_from_env():
    """flash_oci should read OCI_USERNAME/OCI_PASSWORD from env when not explicitly provided."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with (
        patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}),
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),patch(
        "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process
    ) as mock_exec
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/private/image:tag")

        env = mock_exec.call_args.kwargs["env"]
        assert env["FLS_REGISTRY_USERNAME"] == "envuser"
        assert env["FLS_REGISTRY_PASSWORD"] == "envpass"


@pytest.mark.anyio
async def test_flash_oci_explicit_credentials_override_env():
    """Explicit credentials should take precedence over env vars."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with (
        patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}),
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),patch(
        "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process
    ) as mock_exec
    ):
        await _collect_flash_oci(
            flasher,
            "oci://quay.io/private/image:tag",
            oci_username="explicit_user",
            oci_password="explicit_pass",
        )

        env = mock_exec.call_args.kwargs["env"]
        assert env["FLS_REGISTRY_USERNAME"] == "explicit_user"
        assert env["FLS_REGISTRY_PASSWORD"] == "explicit_pass"


@pytest.mark.anyio
async def test_flash_oci_streams_output():
    """flash_oci should yield stdout and stderr chunks as they arrive."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(
        stdout_lines=["downloading layer 1\n", "downloading layer 2\n"],
        stderr_lines=["progress: 50%\n", "progress: 100%\n"],
    )

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
    ):
        results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

        # Should have received streaming output plus the final returncode chunk
        stdout_chunks = [r[0] for r in results if r[0]]
        stderr_chunks = [r[1] for r in results if r[1]]
        assert len(stdout_chunks) > 0
        assert len(stderr_chunks) > 0
        assert any(r[2] == 0 for r in results)


@pytest.mark.anyio
async def test_flash_oci_rejects_non_oci_url():
    """URLs without oci:// prefix should be rejected."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        async for _ in flasher.flash_oci("docker://image:tag"):
            pass

    with pytest.raises(ValueError, match="OCI URL must start with oci://"):
        async for _ in flasher.flash_oci("quay.io/org/image:tag"):
            pass


@pytest.mark.anyio
async def test_flash_oci_partial_credentials_rejected():
    """Providing only username or only password should be rejected."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="OCI authentication requires both"):
        async for _ in flasher.flash_oci("oci://image:tag", oci_username="user", oci_password=None):
            pass

    with pytest.raises(ValueError, match="OCI authentication requires both"):
        async for _ in flasher.flash_oci("oci://image:tag", oci_username=None, oci_password="pass"):
            pass


@pytest.mark.anyio
async def test_flash_oci_fls_failure():
    """Non-zero return code from fls should raise RuntimeError."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(returncode=1)

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
        pytest.raises(RuntimeError, match="fls flash failed"),
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_fls_timeout():
    """Flash should raise RuntimeError when timeout is exceeded."""
    driver = Qemu(flash_timeout=0)  # Immediate timeout
    flasher = driver.children["flasher"]

    async def hanging_readline():
        await asyncio.sleep(10)
        return b""

    mock_process = MagicMock()
    mock_process.returncode = None

    async def mock_wait():
        mock_process.returncode = -9
        return -9

    mock_process.wait = mock_wait
    mock_process.kill = MagicMock()

    stdout_stream = MagicMock()
    stdout_stream.readline = hanging_readline
    mock_process.stdout = stdout_stream

    stderr_stream = MagicMock()
    stderr_stream.readline = hanging_readline
    mock_process.stderr = stderr_stream

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
    ):
        with pytest.raises(RuntimeError, match="fls flash timed out"):
            await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

        mock_process.kill.assert_called_once()


@pytest.mark.anyio
async def test_flash_oci_inner_wait_timeout():
    """Inner wait_for timeout should continue the loop without raising."""
    driver = Qemu(flash_timeout=600)
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process(stdout_lines=["output\n"])

    original_wait_for = asyncio.wait_for
    timeout_fired = False

    async def mock_wait_for(awaitable, *, timeout):
        nonlocal timeout_fired
        if not timeout_fired:
            timeout_fired = True
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise TimeoutError()
        return await original_wait_for(awaitable, timeout=timeout)

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
        patch("asyncio.wait_for", mock_wait_for),
    ):
        results = await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

        assert timeout_fired
        assert any(r[2] == 0 for r in results)


@pytest.mark.anyio
async def test_flash_oci_process_cleanup_on_early_exit():
    """Finally block should kill process when generator is abandoned early."""
    driver = Qemu()
    flasher = driver.children["flasher"]

    mock_process = MagicMock()
    mock_process.returncode = None

    async def mock_wait():
        mock_process.returncode = 0
        return 0

    mock_process.wait = mock_wait
    mock_process.kill = MagicMock()

    stdout_stream = MagicMock()
    stdout_stream.readline = AsyncMock(side_effect=[b"line1\n", b"line2\n", b""])
    mock_process.stdout = stdout_stream

    stderr_stream = MagicMock()
    stderr_stream.readline = AsyncMock(side_effect=[b""])
    mock_process.stderr = stderr_stream

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
        gen = flasher._stream_subprocess(["fls", "from-url", "oci://img", "/tmp/root"], None)  # ty: ignore[unresolved-attribute]
        async for _ in gen:
            break
        await gen.aclose()

        mock_process.kill.assert_called()


@pytest.mark.anyio
async def test_flash_oci_fls_not_found():
    """FileNotFoundError should raise RuntimeError with install hint."""
    driver = Qemu()
    flasher = driver.children["flasher"]

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"),
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=FileNotFoundError),
        pytest.raises(RuntimeError, match="fls command not found"),
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")


@pytest.mark.anyio
async def test_flash_oci_uses_fls_config():
    """flash_oci should pass fls config from parent Qemu driver."""
    driver = Qemu(fls_version="0.2.0")
    flasher = driver.children["flasher"]
    mock_process = _create_mock_process()

    with (
        patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls") as mock_get,
        patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process),
    ):
        await _collect_flash_oci(flasher, "oci://quay.io/org/image:tag")

        mock_get.assert_called_once_with(
            fls_version="0.2.0",
            fls_binary_url=None,
            allow_custom_binaries=False,
        )


@pytest.mark.anyio
async def test_flash_oci_invalid_partition():
    """Invalid partition names should raise ValueError."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    with pytest.raises(ValueError, match="invalid partition name"):
        async for _ in flasher.flash_oci("oci://image:tag", partition="nonexistent"):
            pass


# OCI Client Integration Tests


def test_flash_oci_via_flasher_client():
    """flasher.flash('oci://...') should route through flash_oci on the driver."""
    mock_process = _create_mock_process(stdout_lines=["done\n"])

    with serve(Qemu()) as qemu, patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"), patch(
        "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process
    ) as mock_exec:
        qemu.flasher.flash("oci://quay.io/org/image:tag")

        mock_exec.assert_called_once()
        assert mock_exec.call_args.args[1] == "from-url"
        assert mock_exec.call_args.args[2] == "oci://quay.io/org/image:tag"


def test_flash_oci_convenience_method():
    """qemu.flash_oci() should delegate to flasher.flash()."""
    mock_process = _create_mock_process()

    with serve(Qemu()) as qemu, patch("jumpstarter_driver_qemu.driver.get_fls_binary", return_value="fls"), patch(
        "asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process
    ) as mock_exec:
        qemu.flash_oci("oci://quay.io/org/image:tag", partition="bios")

        mock_exec.assert_called_once()
        assert mock_exec.call_args.args[1] == "from-url"
        assert mock_exec.call_args.args[2] == "oci://quay.io/org/image:tag"
        assert Path(mock_exec.call_args.args[3]).name == "bios"


@pytest.mark.anyio
async def test_flash_routes_oci_to_flash_oci():
    """Driver-side flash() should detect oci:// URLs and route to flash_oci."""
    driver = Qemu()
    flasher = driver.children["flasher"]
    assert isinstance(flasher, QemuFlasher)

    async def mock_generator(*args, **kwargs):
        yield "", "", 0

    mock = MagicMock(side_effect=mock_generator)

    with patch.object(flasher, "flash_oci", mock):
        await flasher.flash("oci://quay.io/org/image:tag", partition="root")

        mock.assert_called_once_with("oci://quay.io/org/image:tag", "root")
