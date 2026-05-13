"""Unit tests for the tcpdump feature of the DUT Network driver.

Tests config validation (enable_tcpdump gating), argument sanitization,
and the streaming driver method using mocked subprocesses.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import anyio.abc
import pytest

from .driver import DutNetwork

_DRIVER_MODULE = "jumpstarter_driver_dut_network.driver"


def _make_driver(tmp_path, **overrides):
    """Create a DutNetwork driver with all system calls mocked."""
    params = {
        "interface": "eth-dut",
        "subnet": "192.168.100.0/24",
        "gateway_ip": "192.168.100.1",
        "upstream_interface": "eth-up",
        "nat_mode": "masquerade",
        "dhcp_enabled": True,
        "dhcp_range_start": "192.168.100.100",
        "dhcp_range_end": "192.168.100.200",
        "addresses": [],
        "dns_servers": ["8.8.8.8"],
        "state_dir": str(tmp_path),
    }
    params.update(overrides)

    with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
         patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
         patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
         patch(f"{_DRIVER_MODULE}.nftables") as mock_nftables, \
         patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dnsmasq:
        mock_sys.platform = "linux"
        mock_shutil.which.return_value = "/usr/bin/fake"
        mock_dnsmasq.state_dir_for_interface.return_value = tmp_path
        mock_dnsmasq.start.return_value = MagicMock()
        mock_iproute.detect_upstream_interface.return_value = "eth-up"
        mock_iproute.interface_exists.return_value = False
        mock_iproute.get_interface_addresses.return_value = []
        mock_iproute.get_interface_forwarding.return_value = "0"
        mock_iproute.get_interface_prefix_len.return_value = 24
        mock_nftables.ensure_filter_forward.return_value = []
        mock_nftables.list_rules.return_value = ""
        mock_nftables._table_name_for.return_value = "jumpstarter_eth_dut"
        driver = DutNetwork(**params)  # type: ignore[missing-argument]

    return driver


class TestTcpdumpConfig:
    def test_enable_tcpdump_default_false(self, tmp_path: Path):
        driver = _make_driver(tmp_path)
        assert driver.enable_tcpdump is False

    def test_enable_tcpdump_set_true(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)
        assert driver.enable_tcpdump is True

    def test_tcpdump_missing_binary_raises_when_enabled(self, tmp_path: Path):
        """When enable_tcpdump is True but tcpdump is not installed, raise."""
        with pytest.raises(RuntimeError, match="tcpdump"):
            with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
                 patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
                 patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
                 patch(f"{_DRIVER_MODULE}.nftables") as mock_nftables, \
                 patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dnsmasq:
                mock_sys.platform = "linux"

                def which_side_effect(cmd):
                    if cmd == "tcpdump":
                        return None
                    return "/usr/bin/fake"

                mock_shutil.which.side_effect = which_side_effect
                mock_dnsmasq.state_dir_for_interface.return_value = tmp_path
                mock_dnsmasq.start.return_value = MagicMock()
                mock_iproute.detect_upstream_interface.return_value = "eth-up"
                mock_iproute.interface_exists.return_value = False
                mock_iproute.get_interface_addresses.return_value = []
                mock_iproute.get_interface_forwarding.return_value = "0"
                mock_iproute.get_interface_prefix_len.return_value = 24
                mock_nftables.ensure_filter_forward.return_value = []
                mock_nftables.list_rules.return_value = ""
                mock_nftables._table_name_for.return_value = "jumpstarter_eth_dut"
                DutNetwork(
                    interface="eth-dut",
                    subnet="192.168.100.0/24",
                    gateway_ip="192.168.100.1",
                    upstream_interface="eth-up",
                    nat_mode="masquerade",
                    enable_tcpdump=True,
                    state_dir=str(tmp_path),
                )  # type: ignore[missing-argument]

    def test_tcpdump_missing_binary_ok_when_disabled(self, tmp_path: Path):
        """When enable_tcpdump is False, missing tcpdump binary is fine."""
        with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
             patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
             patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nftables, \
             patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dnsmasq:
            mock_sys.platform = "linux"

            def which_side_effect(cmd):
                if cmd == "tcpdump":
                    return None
                return "/usr/bin/fake"

            mock_shutil.which.side_effect = which_side_effect
            mock_dnsmasq.state_dir_for_interface.return_value = tmp_path
            mock_dnsmasq.start.return_value = MagicMock()
            mock_iproute.detect_upstream_interface.return_value = "eth-up"
            mock_iproute.interface_exists.return_value = False
            mock_iproute.get_interface_addresses.return_value = []
            mock_iproute.get_interface_forwarding.return_value = "0"
            mock_iproute.get_interface_prefix_len.return_value = 24
            mock_nftables.ensure_filter_forward.return_value = []
            mock_nftables.list_rules.return_value = ""
            mock_nftables._table_name_for.return_value = "jumpstarter_eth_dut"
            driver = DutNetwork(
                interface="eth-dut",
                subnet="192.168.100.0/24",
                gateway_ip="192.168.100.1",
                upstream_interface="eth-up",
                nat_mode="masquerade",
                enable_tcpdump=False,
                state_dir=str(tmp_path),
            )  # type: ignore[missing-argument]
            assert driver.enable_tcpdump is False


class TestSanitizeTcpdumpArgs:
    def test_removes_interface_flag(self):
        assert DutNetwork._sanitize_tcpdump_args(["-i", "eth0", "-c", "10"]) == ["-c", "10"]

    def test_removes_long_interface_flag(self):
        assert DutNetwork._sanitize_tcpdump_args(["--interface", "eth0", "-c", "5"]) == ["-c", "5"]

    def test_removes_write_flag(self):
        assert DutNetwork._sanitize_tcpdump_args(["-w", "/tmp/out.pcap", "-c", "10"]) == ["-c", "10"]

    def test_removes_interface_equals_form(self):
        assert DutNetwork._sanitize_tcpdump_args(["-i=eth0", "-c", "10"]) == ["-c", "10"]

    def test_passes_safe_args(self):
        args = ["-c", "10", "-n", "-v", "port", "80"]
        assert DutNetwork._sanitize_tcpdump_args(args) == args

    def test_empty_args(self):
        assert DutNetwork._sanitize_tcpdump_args([]) == []

    def test_multiple_blocked_flags(self):
        args = ["-i", "eth0", "-w", "/tmp/out.pcap", "-c", "5"]
        assert DutNetwork._sanitize_tcpdump_args(args) == ["-c", "5"]


def _create_mock_byte_stream(data: bytes):
    """Create a mock ByteReceiveStream that returns data then raises EndOfStream."""
    stream = MagicMock(spec=anyio.abc.ByteReceiveStream)
    call_count = {"n": 0}

    async def mock_receive(max_bytes=65536):
        if call_count["n"] >= 1:
            raise anyio.EndOfStream()
        call_count["n"] += 1
        return data

    stream.receive = mock_receive
    stream.aclose = AsyncMock()
    return stream


class TestTcpdumpMethod:
    def test_tcpdump_raises_when_disabled(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=False)
        with pytest.raises(RuntimeError, match="tcpdump is not enabled"):
            anyio.run(
                _consume_async_gen, driver.tcpdump()
            )

    def test_tcpdump_streams_output(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        data = (
            b"12:00:00.000000 IP 192.168.100.10 > 8.8.8.8: ICMP echo request\n"
            b"12:00:00.001000 IP 8.8.8.8 > 192.168.100.10: ICMP echo reply\n"
        )
        mock_stdout = _create_mock_byte_stream(data)

        mock_proc = AsyncMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = _create_mock_byte_stream(b"")
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(f"{_DRIVER_MODULE}.anyio.open_process",
                   new_callable=AsyncMock, return_value=mock_proc):
            output = anyio.run(
                _consume_async_gen, driver.tcpdump()
            )

        assert len(output) == 2
        assert "ICMP echo request" in output[0]
        assert "ICMP echo reply" in output[1]

    def test_tcpdump_enforces_interface(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        mock_stdout = _create_mock_byte_stream(b"")

        mock_proc = AsyncMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = _create_mock_byte_stream(b"")
        mock_proc.returncode = 0
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(f"{_DRIVER_MODULE}.anyio.open_process",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            anyio.run(
                _consume_async_gen, driver.tcpdump(args=["-i", "evil-iface", "-c", "1"])
            )

        call_args = mock_exec.call_args[0]
        cmd = list(call_args[0])
        assert cmd[0] == "tcpdump"
        assert "-i" in cmd
        iface_idx = cmd.index("-i")
        assert cmd[iface_idx + 1] == "eth-dut"
        assert cmd.count("-i") == 1

    def test_tcpdump_passes_extra_args(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        mock_stdout = _create_mock_byte_stream(b"")

        mock_proc = AsyncMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = _create_mock_byte_stream(b"")
        mock_proc.returncode = 0
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(f"{_DRIVER_MODULE}.anyio.open_process",
                   new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            anyio.run(
                _consume_async_gen, driver.tcpdump(args=["-c", "10", "-n", "port", "80"])
            )

        call_args = mock_exec.call_args[0]
        cmd = list(call_args[0])
        assert "-c" in cmd
        assert "10" in cmd
        assert "-n" in cmd
        assert "port" in cmd
        assert "80" in cmd


class TestTcpdumpCleanup:
    def test_cleanup_terminates_tcpdump_process(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        driver._tcpdump_process = mock_proc

        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()

        mock_proc.terminate.assert_called_once()
        assert driver._tcpdump_process is None

    def test_cleanup_handles_already_terminated_process(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=True)
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = ProcessLookupError()
        driver._tcpdump_process = mock_proc

        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()  # Should not raise

        assert driver._tcpdump_process is None


async def _consume_async_gen(gen):
    """Helper to consume an async generator and return results as a list."""
    results = []
    async for item in gen:
        results.append(item)
    return results
