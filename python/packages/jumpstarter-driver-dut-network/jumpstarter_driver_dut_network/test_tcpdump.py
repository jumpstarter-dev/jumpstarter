"""Unit tests for the tcpdump feature of the DUT Network driver.

Tests config validation (enable_tcpdump gating), argument sanitization,
and the streaming driver method using mocked subprocesses.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from anyio import fail_after

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
        driver = DutNetwork(**params)  # ty: ignore[invalid-argument-type]

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
        with pytest.raises(RuntimeError, match="tcpdump"), patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
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
            )

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
            )
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


FAKE_TCPDUMP = """\
import os, sys, time
mode = os.environ["FAKE_TCPDUMP"]
if mode == "args":
    print(" ".join(sys.argv[1:]), flush=True)
elif mode == "lines":
    sys.stderr.write("tcpdump: listening on eth-dut\\n")
    sys.stderr.flush()
    print("12:00:00.000000 IP 192.168.100.10 > 8.8.8.8: ICMP echo request", flush=True)
    print("12:00:00.001000 IP 8.8.8.8 > 192.168.100.10: ICMP echo reply", flush=True)
elif mode == "forever":
    print(os.getpid(), flush=True)
    time.sleep(60)
"""


@pytest.fixture
def fake_tcpdump(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tcpdump = bin_dir / "tcpdump"
    tcpdump.write_text(f"#!{sys.executable}\n{FAKE_TCPDUMP}")
    tcpdump.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return lambda mode: monkeypatch.setenv("FAKE_TCPDUMP", mode)


class TestTcpdumpMethod:
    @pytest.mark.anyio
    async def test_tcpdump_raises_when_disabled(self, tmp_path: Path):
        driver = _make_driver(tmp_path, enable_tcpdump=False)
        with pytest.raises(RuntimeError, match="tcpdump is not enabled"):
            await _consume_async_gen(driver.tcpdump())

    @pytest.mark.anyio
    async def test_tcpdump_streams_output_lines_with_stderr_merged(self, tmp_path: Path, fake_tcpdump):
        fake_tcpdump("lines")
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        output = await _consume_async_gen(driver.tcpdump())

        assert output == [
            "tcpdump: listening on eth-dut",
            "12:00:00.000000 IP 192.168.100.10 > 8.8.8.8: ICMP echo request",
            "12:00:00.001000 IP 8.8.8.8 > 192.168.100.10: ICMP echo reply",
        ]

    @pytest.mark.anyio
    async def test_tcpdump_enforces_interface(self, tmp_path: Path, fake_tcpdump):
        fake_tcpdump("args")
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        [args] = await _consume_async_gen(driver.tcpdump(args=["-i", "evil-iface", "-c", "1"]))

        cmd = args.split()
        assert cmd[cmd.index("-i") + 1] == "eth-dut"
        assert cmd.count("-i") == 1

    @pytest.mark.anyio
    async def test_tcpdump_passes_extra_args(self, tmp_path: Path, fake_tcpdump):
        fake_tcpdump("args")
        driver = _make_driver(tmp_path, enable_tcpdump=True)

        [args] = await _consume_async_gen(driver.tcpdump(args=["-c", "10", "-n", "port", "80"]))

        assert args.endswith("-c 10 -n port 80")

    @pytest.mark.anyio
    async def test_tcpdump_stops_when_the_consumer_stops(self, tmp_path: Path, fake_tcpdump):
        fake_tcpdump("forever")
        driver = _make_driver(tmp_path, enable_tcpdump=True)
        stream = driver.tcpdump()

        with fail_after(10):
            pid = int(await stream.__anext__())
            assert driver._tcpdump_process is not None
            await stream.aclose()

        assert driver._tcpdump_process is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


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
