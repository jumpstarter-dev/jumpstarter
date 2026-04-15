from __future__ import annotations

import shutil
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jumpstarter_driver_renode.driver import (
    Renode,
    RenodeFlasher,
    RenodePower,
    _detect_load_command,
    _find_renode,
)
from jumpstarter_driver_renode.monitor import RenodeMonitor, RenodeMonitorError

from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestRenodeMonitor:
    @pytest.mark.anyio
    async def test_monitor_connect_retry(self):
        """Monitor retries on OSError until connection succeeds."""
        monitor = RenodeMonitor()
        call_count = 0

        async def mock_connect_tcp(host, port):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OSError("Connection refused")
            stream = AsyncMock()
            stream.receive = AsyncMock(return_value=b"Renode v1.15\n(monitor) \n")
            return stream

        with patch(
            "jumpstarter_driver_renode.monitor.connect_tcp",
            side_effect=mock_connect_tcp,
        ):
            with patch("jumpstarter_driver_renode.monitor.sleep", new_callable=AsyncMock):
                await monitor.connect("127.0.0.1", 12345)

        assert call_count == 3
        assert monitor._stream is not None

    @pytest.mark.anyio
    async def test_monitor_execute_command(self):
        """Execute sends command and returns response text."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        responses = iter([b"some output\n(monitor) \n", b""])
        stream.receive = AsyncMock(side_effect=lambda size: next(responses))
        monitor._stream = stream
        monitor._buffer = b""

        result = await monitor.execute("mach create")
        stream.send.assert_called_once_with(b"mach create\n")
        assert "some output" in result

    @pytest.mark.anyio
    async def test_monitor_execute_error_response(self):
        """Monitor raises RenodeMonitorError on error responses."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        stream.receive = AsyncMock(return_value=b"Could not find peripheral\n(monitor) \n")
        monitor._stream = stream
        monitor._buffer = b""

        with pytest.raises(RenodeMonitorError, match="Could not find peripheral"):
            await monitor.execute("bad command")

    @pytest.mark.anyio
    async def test_monitor_execute_not_connected(self):
        """Execute raises RuntimeError when not connected."""
        monitor = RenodeMonitor()
        with pytest.raises(RuntimeError, match="not connected"):
            await monitor.execute("help")

    @pytest.mark.anyio
    async def test_monitor_disconnect(self):
        """Disconnect closes stream and is idempotent."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        monitor._stream = stream

        await monitor.disconnect()
        stream.aclose.assert_called_once()
        assert monitor._stream is None

        await monitor.disconnect()

    @pytest.mark.anyio
    async def test_monitor_disconnect_ignores_errors(self):
        """Disconnect handles errors during close gracefully."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        stream.aclose = AsyncMock(side_effect=OSError("already closed"))
        monitor._stream = stream

        await monitor.disconnect()
        assert monitor._stream is None

    @pytest.mark.anyio
    async def test_monitor_execute_rejects_newlines(self):
        """execute() rejects commands containing newline characters."""
        monitor = RenodeMonitor()
        monitor._stream = AsyncMock()

        with pytest.raises(ValueError, match="newline"):
            await monitor.execute("cmd1\ncmd2")

        with pytest.raises(ValueError, match="newline"):
            await monitor.execute("cmd1\rcmd2")

    @pytest.mark.anyio
    async def test_monitor_connect_closes_stream_on_retry(self):
        """connect() closes the previous stream before retrying."""
        monitor = RenodeMonitor()
        streams = []
        call_count = 0

        async def mock_connect_tcp(host, port):
            nonlocal call_count
            call_count += 1
            stream = AsyncMock()
            streams.append(stream)
            if call_count < 2:
                stream.receive = AsyncMock(side_effect=OSError("not ready"))
            else:
                stream.receive = AsyncMock(return_value=b"Renode v1.15\n(monitor) \n")
            return stream

        with patch(
            "jumpstarter_driver_renode.monitor.connect_tcp",
            side_effect=mock_connect_tcp,
        ):
            with patch("jumpstarter_driver_renode.monitor.sleep", new_callable=AsyncMock):
                await monitor.connect("127.0.0.1", 12345)

        streams[0].aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_monitor_error_detection_per_line(self):
        """Error markers are detected even when not on the first line."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        stream.receive = AsyncMock(return_value=b"info text\nError executing command\n(monitor) \n")
        monitor._stream = stream
        monitor._buffer = b""

        with pytest.raises(RenodeMonitorError, match="Error executing"):
            await monitor.execute("bad command")

    def test_monitor_prompt_matches_expected_only(self):
        """_is_prompt only matches prompts in the expected set."""
        monitor = RenodeMonitor()
        assert monitor._is_prompt(b"(monitor)") is True
        assert monitor._is_prompt(b"(default)") is False
        assert monitor._is_prompt(b"(enabled)") is False

        monitor.add_expected_prompt("my-machine")
        assert monitor._is_prompt(b"(my-machine)") is True
        assert monitor._is_prompt(b"(other)") is False

    @pytest.mark.anyio
    async def test_connect_timeout_on_persistent_error(self):
        """connect() raises TimeoutError when OSError persists."""
        monitor = RenodeMonitor()

        async def always_fail(host, port):
            raise OSError("Connection refused")

        with patch(
            "jumpstarter_driver_renode.monitor.connect_tcp",
            side_effect=always_fail,
        ):
            with pytest.raises(TimeoutError):
                await monitor.connect("127.0.0.1", 12345, timeout=0.5)

    @pytest.mark.anyio
    async def test_read_until_prompt_connection_closed(self):
        """_read_until_prompt raises ConnectionError on empty receive."""
        monitor = RenodeMonitor()
        stream = AsyncMock()
        stream.receive = AsyncMock(return_value=b"")
        monitor._stream = stream
        monitor._buffer = b""

        with pytest.raises(ConnectionError, match="connection closed"):
            await monitor._read_until_prompt()

    def test_close_sync_closes_raw_socket(self):
        """close_sync() closes the underlying socket and clears state."""
        monitor = RenodeMonitor()
        mock_socket = MagicMock()
        stream = MagicMock()
        stream.extra = MagicMock(return_value=mock_socket)
        monitor._stream = stream
        monitor._buffer = b"leftover"

        monitor.close_sync()

        mock_socket.close.assert_called_once()
        assert monitor._stream is None
        assert monitor._buffer == b""

    def test_close_sync_no_stream(self):
        """close_sync() is safe to call when not connected."""
        monitor = RenodeMonitor()
        monitor.close_sync()
        assert monitor._stream is None


def _make_driver(**kwargs) -> Renode:
    defaults = {"platform": "platforms/boards/stm32f4_discovery-kit.repl"}
    defaults.update(kwargs)
    return Renode(**defaults)


class TestRenodePower:
    @pytest.mark.anyio
    async def test_power_on_command_sequence(self):
        """Verify the exact sequence of monitor commands during power on."""
        driver = _make_driver(uart="sysbus.usart2")
        driver._firmware_path = "/tmp/test.elf"
        driver._load_command = "sysbus LoadELF"
        power: RenodePower = driver.children["power"]

        mock_monitor = AsyncMock(spec=RenodeMonitor)

        with patch(
            "jumpstarter_driver_renode.driver._find_renode",
            return_value="/usr/bin/renode",
        ):
            with patch(
                "jumpstarter_driver_renode.driver._find_free_port",
                return_value=54321,
            ):
                with patch("jumpstarter_driver_renode.driver.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock()
                    with patch(
                        "jumpstarter_driver_renode.driver.RenodeMonitor",
                        return_value=mock_monitor,
                    ):
                        await power.on()

        calls = [c.args[0] for c in mock_monitor.execute.call_args_list]
        assert calls[0] == 'mach create "machine-0"'
        assert "LoadPlatformDescription" in calls[1]
        assert "stm32f4_discovery-kit.repl" in calls[1]
        assert "CreateUartPtyTerminal" in calls[2]
        assert "connector Connect sysbus.usart2 term" == calls[3]
        assert "LoadELF" in calls[4]
        assert calls[5] == "start"

    @pytest.mark.anyio
    async def test_power_on_with_extra_commands(self):
        """Extra commands are sent between connector Connect and LoadELF."""
        driver = _make_driver(extra_commands=["sysbus WriteDoubleWord 0x40090030 0x0301"])
        driver._firmware_path = "/tmp/test.elf"
        power: RenodePower = driver.children["power"]
        mock_monitor = AsyncMock(spec=RenodeMonitor)

        with patch(
            "jumpstarter_driver_renode.driver._find_renode",
            return_value="/usr/bin/renode",
        ):
            with patch(
                "jumpstarter_driver_renode.driver._find_free_port",
                return_value=54321,
            ):
                with patch("jumpstarter_driver_renode.driver.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock()
                    with patch(
                        "jumpstarter_driver_renode.driver.RenodeMonitor",
                        return_value=mock_monitor,
                    ):
                        await power.on()

        calls = [c.args[0] for c in mock_monitor.execute.call_args_list]
        connect_idx = next(i for i, c in enumerate(calls) if "connector Connect" in c)
        load_idx = next(i for i, c in enumerate(calls) if "LoadELF" in c)
        extra_idx = next(i for i, c in enumerate(calls) if "WriteDoubleWord" in c)
        assert connect_idx < extra_idx < load_idx

    @pytest.mark.anyio
    async def test_power_on_without_firmware(self):
        """When no firmware is set, LoadELF is skipped but start is sent."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        mock_monitor = AsyncMock(spec=RenodeMonitor)

        with patch(
            "jumpstarter_driver_renode.driver._find_renode",
            return_value="/usr/bin/renode",
        ):
            with patch(
                "jumpstarter_driver_renode.driver._find_free_port",
                return_value=54321,
            ):
                with patch("jumpstarter_driver_renode.driver.Popen") as mock_popen:
                    mock_popen.return_value = MagicMock()
                    with patch(
                        "jumpstarter_driver_renode.driver.RenodeMonitor",
                        return_value=mock_monitor,
                    ):
                        await power.on()

        calls = [c.args[0] for c in mock_monitor.execute.call_args_list]
        assert not any("LoadELF" in c for c in calls)
        assert calls[-1] == "start"

    @pytest.mark.anyio
    async def test_power_on_idempotent(self):
        """Second on() call logs warning and does nothing."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        power._process = MagicMock()

        with patch("jumpstarter_driver_renode.driver.Popen") as mock_popen:
            with patch("jumpstarter_driver_renode.driver.RenodeMonitor") as mock_monitor_cls:
                await power.on()

        mock_popen.assert_not_called()
        mock_monitor_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_power_off_terminates_process(self):
        """off() terminates the process, waits, then kills on timeout."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        mock_process = MagicMock()
        mock_process.terminate = MagicMock()
        mock_process.wait = MagicMock(side_effect=TimeoutExpired("renode", 5))
        mock_process.kill = MagicMock()
        power._process = mock_process

        mock_monitor = AsyncMock(spec=RenodeMonitor)
        power._monitor = mock_monitor

        await power.off()

        mock_monitor.execute.assert_called_with("quit")
        mock_monitor.disconnect.assert_called_once()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        assert power._process is None

    @pytest.mark.anyio
    async def test_power_off_clean_shutdown(self):
        """off() with clean process exit does not call kill()."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        mock_process = MagicMock()
        mock_process.wait = MagicMock()
        power._process = mock_process
        power._monitor = AsyncMock(spec=RenodeMonitor)

        await power.off()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_not_called()
        assert power._process is None

    @pytest.mark.anyio
    async def test_power_off_idempotent(self):
        """Second off() call logs warning and does nothing."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        power._process = None

        await power.off()

        assert power._process is None
        assert power._monitor is None

    @pytest.mark.anyio
    async def test_power_close_calls_off(self):
        """close() terminates the process."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        mock_process = MagicMock()
        mock_process.wait = MagicMock()
        power._process = mock_process

        power.close()

        mock_process.terminate.assert_called_once()
        assert power._process is None

    def test_close_kills_on_timeout(self):
        """close() kills process when wait() times out."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        mock_process = MagicMock()
        mock_process.wait = MagicMock(side_effect=TimeoutExpired("renode", 5))
        power._process = mock_process

        power.close()

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
        assert power._process is None

    def test_close_cleans_up_monitor_socket(self):
        """close() calls close_sync() on the monitor before terminating."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        mock_process = MagicMock()
        mock_process.wait = MagicMock()
        power._process = mock_process
        mock_monitor = MagicMock(spec=RenodeMonitor)
        power._monitor = mock_monitor

        power.close()

        mock_monitor.close_sync.assert_called_once()
        assert power._monitor is None
        assert power._process is None

    @pytest.mark.anyio
    async def test_power_on_cleanup_on_failure(self):
        """on() cleans up process when monitor setup fails."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        mock_monitor = AsyncMock(spec=RenodeMonitor)
        mock_monitor.execute.side_effect = RenodeMonitorError("setup failed")
        mock_process = MagicMock()
        mock_process.wait = MagicMock()

        with patch(
            "jumpstarter_driver_renode.driver._find_renode",
            return_value="/usr/bin/renode",
        ):
            with patch(
                "jumpstarter_driver_renode.driver._find_free_port",
                return_value=54321,
            ):
                with patch(
                    "jumpstarter_driver_renode.driver.Popen",
                    return_value=mock_process,
                ):
                    with patch(
                        "jumpstarter_driver_renode.driver.RenodeMonitor",
                        return_value=mock_monitor,
                    ):
                        with pytest.raises(RenodeMonitorError):
                            await power.on()

        assert power._process is None
        assert power._monitor is None
        mock_process.terminate.assert_called_once()

    @pytest.mark.anyio
    async def test_off_cleans_up_on_terminate_failure(self):
        """off() resets _process to None even if terminate() raises."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        mock_process = MagicMock()
        mock_process.terminate = MagicMock(side_effect=ProcessLookupError)
        power._process = mock_process
        power._monitor = AsyncMock(spec=RenodeMonitor)

        await power.off()

        assert power._process is None
        assert power._monitor is None

    @pytest.mark.anyio
    async def test_power_read_not_implemented(self):
        """read() raises NotImplementedError."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        with pytest.raises(NotImplementedError):
            await power.read()

    def test_is_running_property(self):
        """is_running reflects process and monitor state."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]

        assert power.is_running is False

        power._process = MagicMock()
        assert power.is_running is False

        power._monitor = MagicMock()
        assert power.is_running is True

        power._process = None
        assert power.is_running is False


class TestRenodeFlasher:
    @pytest.mark.anyio
    async def test_flash_stores_firmware_path(self, tmp_path):
        """flash() writes firmware to temp dir and stores the path."""
        driver = _make_driver()

        firmware_data = b"\x00" * 64
        firmware_file = tmp_path / "test.elf"
        firmware_file.write_bytes(firmware_data)

        flasher: RenodeFlasher = driver.children["flasher"]

        with patch.object(flasher, "resource") as mock_resource:
            mock_res = AsyncMock()
            mock_res.__aiter__ = lambda self: self
            mock_res.__anext__ = AsyncMock(side_effect=[firmware_data, StopAsyncIteration()])
            mock_resource.return_value.__aenter__ = AsyncMock(return_value=mock_res)
            mock_resource.return_value.__aexit__ = AsyncMock()

            await flasher.flash(str(firmware_file))

        assert driver._firmware_path is not None
        assert Path(driver._firmware_path).name == "firmware"

    @pytest.mark.anyio
    async def test_flash_while_running_sends_load_and_reset(self):
        """When simulation is running, flash() sends load + Reset."""
        driver = _make_driver()
        power: RenodePower = driver.children["power"]
        power._process = MagicMock()
        mock_monitor = AsyncMock(spec=RenodeMonitor)
        power._monitor = mock_monitor

        flasher: RenodeFlasher = driver.children["flasher"]
        elf_data = b"\x7fELF" + b"\x00" * 60

        with patch.object(flasher, "resource") as mock_resource:
            mock_res = AsyncMock()
            mock_res.__aiter__ = lambda self: self
            mock_res.__anext__ = AsyncMock(side_effect=[elf_data, StopAsyncIteration()])
            mock_resource.return_value.__aenter__ = AsyncMock(return_value=mock_res)
            mock_resource.return_value.__aexit__ = AsyncMock()

            await flasher.flash("/some/firmware.elf")

        calls = [c.args[0] for c in mock_monitor.execute.call_args_list]
        assert any("LoadELF" in c for c in calls)
        assert any("Reset" in c for c in calls)

    @pytest.mark.anyio
    async def test_flash_custom_load_command(self):
        """flash() uses custom load_command when provided."""
        driver = _make_driver()
        flasher: RenodeFlasher = driver.children["flasher"]

        with patch.object(flasher, "resource") as mock_resource:
            mock_res = AsyncMock()
            mock_res.__aiter__ = lambda self: self
            mock_res.__anext__ = AsyncMock(side_effect=[b"\x00", StopAsyncIteration()])
            mock_resource.return_value.__aenter__ = AsyncMock(return_value=mock_res)
            mock_resource.return_value.__aexit__ = AsyncMock()

            await flasher.flash(
                "/some/firmware.bin",
                load_command="sysbus LoadBinary",
            )

        assert driver._load_command == "sysbus LoadBinary"

    @pytest.mark.anyio
    async def test_flash_rejects_invalid_load_command(self):
        """flash() rejects load_command values not in the allowlist."""
        driver = _make_driver()
        flasher: RenodeFlasher = driver.children["flasher"]

        with pytest.raises(ValueError, match="unsupported load_command"):
            await flasher.flash("/some/fw.elf", load_command="logFile @/tmp/evil")

    @pytest.mark.anyio
    async def test_dump_not_implemented(self):
        """dump() raises NotImplementedError."""
        driver = _make_driver()
        flasher: RenodeFlasher = driver.children["flasher"]

        with pytest.raises(NotImplementedError, match="not supported"):
            await flasher.dump("/dev/null")

    def test_detect_load_command_elf(self, tmp_path):
        """ELF files are detected and use sysbus LoadELF."""
        elf = tmp_path / "fw.elf"
        elf.write_bytes(b"\x7fELF" + b"\x00" * 60)
        assert _detect_load_command(str(elf)) == "sysbus LoadELF"

    def test_detect_load_command_binary(self, tmp_path):
        """Non-ELF files default to sysbus LoadBinary."""
        raw = tmp_path / "fw.bin"
        raw.write_bytes(b"\x00" * 64)
        assert _detect_load_command(str(raw)) == "sysbus LoadBinary"


class TestRenodeConfig:
    def test_renode_defaults(self):
        """Default values are set correctly."""
        driver = _make_driver()
        assert driver.uart == "sysbus.uart0"
        assert driver.machine_name == "machine-0"
        assert driver.monitor_port == 0
        assert driver.extra_commands == []
        assert driver.allow_raw_monitor is False
        assert driver._firmware_path is None

    def test_renode_children_wired(self):
        """Children drivers are wired correctly."""
        driver = _make_driver()
        assert "power" in driver.children
        assert "flasher" in driver.children
        assert "console" in driver.children
        assert isinstance(driver.children["power"], RenodePower)
        assert isinstance(driver.children["flasher"], RenodeFlasher)

    def test_renode_custom_config(self):
        """Custom config values are applied."""
        driver = _make_driver(
            uart="sysbus.usart2",
            machine_name="my-machine",
            monitor_port=9999,
            extra_commands=["command1", "command2"],
        )
        assert driver.uart == "sysbus.usart2"
        assert driver.machine_name == "my-machine"
        assert driver.monitor_port == 9999
        assert driver.extra_commands == ["command1", "command2"]

    def test_renode_pty_path(self):
        """PTY path is inside the temp directory."""
        driver = _make_driver()
        pty_path = Path(driver._pty)
        assert pty_path == Path(driver._tmp_dir.name) / "pty"

    def test_renode_temp_directory_lifecycle(self):
        """TemporaryDirectory is created and can be cleaned up."""
        driver = _make_driver()
        tmp_path = driver._tmp_dir.name
        assert Path(tmp_path).exists()
        driver._tmp_dir.cleanup()
        assert not Path(tmp_path).exists()

    def test_renode_get_platform(self):
        """get_platform returns the platform path."""
        driver = _make_driver()
        assert driver.get_platform() == "platforms/boards/stm32f4_discovery-kit.repl"

    def test_renode_get_uart(self):
        """get_uart returns the UART peripheral path."""
        driver = _make_driver(uart="sysbus.usart3")
        assert driver.get_uart() == "sysbus.usart3"

    def test_renode_get_machine_name(self):
        """get_machine_name returns the machine name."""
        driver = _make_driver(machine_name="test-mcu")
        assert driver.get_machine_name() == "test-mcu"

    def test_find_renode_not_on_path(self):
        """_find_renode raises FileNotFoundError when binary is not on PATH."""
        with patch(
            "jumpstarter_driver_renode.driver.shutil.which",
            return_value=None,
        ):
            with pytest.raises(FileNotFoundError, match="renode executable not found"):
                _find_renode()

    def test_set_firmware(self):
        """set_firmware stores path and command on the driver."""
        driver = _make_driver()
        driver.set_firmware("/tmp/fw.elf", "sysbus LoadELF")
        assert driver._firmware_path == "/tmp/fw.elf"
        assert driver._load_command == "sysbus LoadELF"

    @pytest.mark.anyio
    async def test_monitor_cmd_success(self):
        """monitor_cmd succeeds when allow_raw_monitor is True and running."""
        driver = _make_driver(allow_raw_monitor=True)
        power: RenodePower = driver.children["power"]
        mock_monitor = AsyncMock(spec=RenodeMonitor)
        mock_monitor.execute = AsyncMock(return_value="OK\n")
        power._process = MagicMock()
        power._monitor = mock_monitor

        result = await driver.monitor_cmd("version")
        assert result == "OK\n"
        mock_monitor.execute.assert_called_once_with("version")


@pytest.mark.skipif(
    shutil.which("renode") is None,
    reason="Renode not installed",
)
def test_driver_renode_e2e(tmp_path):
    """E2E: start Renode, verify power on/off cycle via serve()."""
    with serve(
        Renode(
            platform="platforms/boards/stm32f4_discovery-kit.repl",
            uart="sysbus.usart2",
        )
    ) as renode:
        assert renode.platform == "platforms/boards/stm32f4_discovery-kit.repl"
        assert renode.uart == "sysbus.usart2"
        assert renode.machine_name == "machine-0"

        renode.power.on()
        renode.power.off()


class TestRenodeClient:
    def test_client_serve_properties(self):
        """Client properties round-trip through serve()."""
        driver = _make_driver(uart="sysbus.usart2")

        with serve(driver) as client:
            assert client.platform == "platforms/boards/stm32f4_discovery-kit.repl"
            assert client.uart == "sysbus.usart2"
            assert client.machine_name == "machine-0"

    def test_client_children_accessible(self):
        """Composite client exposes power, flasher, console children."""
        driver = _make_driver()

        with serve(driver) as client:
            assert hasattr(client, "power")
            assert hasattr(client, "flasher")
            assert hasattr(client, "console")

    def test_client_monitor_cmd_disabled_by_default(self):
        """monitor_cmd raises when allow_raw_monitor is False (default)."""
        from jumpstarter.client.core import DriverError

        driver = _make_driver()

        with serve(driver) as client:
            with pytest.raises(DriverError, match="raw monitor access is disabled"):
                client.monitor_cmd("help")

    def test_client_monitor_cmd_not_running(self):
        """monitor_cmd raises when Renode is not running (but monitor enabled)."""
        from jumpstarter.client.core import DriverError

        driver = _make_driver(allow_raw_monitor=True)

        with serve(driver) as client:
            with pytest.raises(DriverError, match="not running"):
                client.monitor_cmd("help")

    def test_client_cli_renders(self):
        """CLI group includes monitor command."""
        from click.testing import CliRunner

        driver = _make_driver()

        with serve(driver) as client:
            cli = client.cli()
            runner = CliRunner()
            result = runner.invoke(cli, ["--help"])
            assert result.exit_code == 0
            assert "monitor" in result.output

    def test_client_cli_monitor_help(self):
        """Monitor CLI subcommand shows help."""
        from click.testing import CliRunner

        driver = _make_driver()

        with serve(driver) as client:
            cli = client.cli()
            runner = CliRunner()
            result = runner.invoke(cli, ["monitor", "--help"])
            assert result.exit_code == 0
            assert "COMMAND" in result.output
