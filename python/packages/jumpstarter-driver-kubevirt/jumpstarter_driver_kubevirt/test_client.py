from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import EndOfStream

from jumpstarter_driver_kubevirt.client import (
    ConsoleExit,
    KubevirtClient,
    KubevirtConsoleClient,
    KubevirtPowerClient,
    KubevirtStorageClient,
)


class TestKubevirtPowerClient:
    def test_is_power_client_subclass(self):
        from jumpstarter_driver_power.client import PowerClient

        assert issubclass(KubevirtPowerClient, PowerClient)


class TestConsoleExit:
    def test_is_exception(self):
        assert issubclass(ConsoleExit, Exception)


class TestKubevirtConsoleClient:
    def test_is_network_client_subclass(self):
        from jumpstarter_driver_network.client import NetworkClient

        assert issubclass(KubevirtConsoleClient, NetworkClient)

    def test_has_cli_method(self):
        assert hasattr(KubevirtConsoleClient, "cli")
        assert callable(KubevirtConsoleClient.cli)

    @pytest.mark.asyncio
    async def test_stream_to_stdout_reads_until_eos(self):
        stream = AsyncMock()
        stream.receive = AsyncMock(side_effect=[b"hello", b"world", EndOfStream()])

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer.write = MagicMock()
            mock_stdout.buffer.flush = MagicMock()

            await KubevirtConsoleClient._stream_to_stdout(stream)

            assert mock_stdout.buffer.write.call_count == 2
            mock_stdout.buffer.write.assert_any_call(b"hello")
            mock_stdout.buffer.write.assert_any_call(b"world")

    @pytest.mark.asyncio
    async def test_stdin_to_stream_ctrl_b_exit(self):
        stream = AsyncMock()

        with (
            patch("os.set_blocking") as mock_set_blocking,
            patch("jumpstarter_driver_kubevirt.client.wait_readable", new_callable=AsyncMock),
            patch("os.read", return_value=bytes([0x02, 0x02, 0x02])),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.fileno.return_value = 0
            with pytest.raises(ConsoleExit):
                await KubevirtConsoleClient._stdin_to_stream(stream)

            mock_set_blocking.assert_called()

    @pytest.mark.asyncio
    async def test_stdin_to_stream_normal_data(self):
        stream = AsyncMock()
        counter = [0]

        async def mock_wait_readable(_fd):
            counter[0] += 1
            if counter[0] > 2:
                raise EndOfStream()

        with (
            patch("os.set_blocking"),
            patch("jumpstarter_driver_kubevirt.client.wait_readable", side_effect=mock_wait_readable),
            patch("os.read", return_value=b"hello"),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.fileno.return_value = 0
            await KubevirtConsoleClient._stdin_to_stream(stream)

            assert stream.send.call_count >= 1

    @pytest.mark.asyncio
    async def test_stdin_to_stream_empty_data_exits(self):
        stream = AsyncMock()

        with (
            patch("os.set_blocking"),
            patch("jumpstarter_driver_kubevirt.client.wait_readable", new_callable=AsyncMock),
            patch("os.read", return_value=b""),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.fileno.return_value = 0
            await KubevirtConsoleClient._stdin_to_stream(stream)

            stream.send.assert_not_called()

    def test_pipe_command_setup(self):
        client = object.__new__(KubevirtConsoleClient)
        client.portal = MagicMock()
        client.stack = MagicMock()

        with patch("jumpstarter_driver_kubevirt.client.NetworkClient.cli") as mock_super:
            import click

            group = click.Group(name="test")
            mock_super.return_value = group
            result = client.cli()
            assert result is group
            cmd_names = list(result.commands.keys())
            assert "pipe" in cmd_names


class TestKubevirtStorageClient:
    def test_is_composite_client_subclass(self):
        from jumpstarter_driver_composite.client import CompositeClient

        assert issubclass(KubevirtStorageClient, CompositeClient)

    def test_has_flash_method(self):
        assert hasattr(KubevirtStorageClient, "flash")
        assert callable(KubevirtStorageClient.flash)

    def test_flash_calls_call(self):
        client = object.__new__(KubevirtStorageClient)
        client.call = MagicMock()
        client.stack = MagicMock()

        client.flash("http://example.com/image.qcow2")
        client.call.assert_called_once_with("flash", "http://example.com/image.qcow2")


class TestKubevirtClient:
    def test_is_composite_client_subclass(self):
        from jumpstarter_driver_composite.client import CompositeClient

        assert issubclass(KubevirtClient, CompositeClient)
