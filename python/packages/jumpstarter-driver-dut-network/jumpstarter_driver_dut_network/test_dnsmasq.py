import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_DNSMASQ_MODULE = "jumpstarter_driver_dut_network.dnsmasq"


class TestWriteConfig:
    def test_basic_config(self, tmp_path: Path):
        from . import dnsmasq

        dnsmasq.write_config(
            state_dir=tmp_path,
            bridge="br-jmp0",
            range_start="192.168.100.100",
            range_end="192.168.100.200",
            static_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10", "hostname": "dut1"}],
            dns_servers=["8.8.8.8"],
            gateway_ip="192.168.100.1",
        )
        content = (tmp_path / "dnsmasq.conf").read_text()
        assert "interface=br-jmp0" in content
        assert "dhcp-range=192.168.100.100,192.168.100.200,12h" in content
        assert "server=8.8.8.8" in content
        assert "dhcp-option=option:router,192.168.100.1" in content
        assert "dhcp-hostsfile=" in content
        hosts = (tmp_path / "dhcp-hosts").read_text()
        assert "aa:bb:cc:dd:ee:ff,192.168.100.10,dut1" in hosts

    def test_lease_without_hostname(self, tmp_path: Path):
        from . import dnsmasq

        dnsmasq.write_config(
            state_dir=tmp_path, bridge="br0", range_start="10.0.0.100",
            range_end="10.0.0.200", static_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.10"}],
            dns_servers=[], gateway_ip="10.0.0.1",
        )
        hosts = (tmp_path / "dhcp-hosts").read_text()
        assert "aa:bb:cc:dd:ee:ff,10.0.0.10\n" in hosts

    def test_dns_entries(self, tmp_path: Path):
        from . import dnsmasq

        dnsmasq.write_config(
            state_dir=tmp_path, bridge="br0", range_start="10.0.0.100",
            range_end="10.0.0.200", static_leases=[], dns_servers=[],
            gateway_ip="10.0.0.1",
            dns_entries=[
                {"hostname": "registry.lab.local", "ip": "10.0.0.5"},
                {"hostname": "controller.lab.local", "ip": "10.0.0.6"},
            ],
        )
        conf = (tmp_path / "dnsmasq.conf").read_text()
        assert "addn-hosts=" in conf
        hosts = (tmp_path / "hosts.local").read_text()
        assert "10.0.0.5 registry.lab.local" in hosts
        assert "10.0.0.6 controller.lab.local" in hosts

    def test_no_dns_entries(self, tmp_path: Path):
        from . import dnsmasq

        dnsmasq.write_config(
            state_dir=tmp_path, bridge="br0", range_start="10.0.0.100",
            range_end="10.0.0.200", static_leases=[], dns_servers=[],
            gateway_ip="10.0.0.1",
        )
        hosts = (tmp_path / "hosts.local").read_text()
        assert hosts == ""


class TestParseLeases:
    def test_parses_lease_file(self, tmp_path: Path):
        from . import dnsmasq

        lease_file = tmp_path / "dnsmasq.leases"
        lease_file.write_text(
            "1717000000 aa:bb:cc:dd:ee:ff 192.168.100.10 dut1 01:aa:bb:cc:dd:ee:ff\n"
            "1717001000 11:22:33:44:55:66 192.168.100.11 * 01:11:22:33:44:55:66\n"
        )
        leases = dnsmasq.parse_leases(tmp_path)
        assert len(leases) == 2
        assert leases[0].mac == "aa:bb:cc:dd:ee:ff"
        assert leases[0].ip == "192.168.100.10"
        assert leases[0].hostname == "dut1"
        assert leases[1].hostname == ""

    def test_returns_empty_when_no_file(self, tmp_path: Path):
        from . import dnsmasq

        assert dnsmasq.parse_leases(tmp_path) == []

    def test_get_lease_by_mac(self, tmp_path: Path):
        from . import dnsmasq

        lease_file = tmp_path / "dnsmasq.leases"
        lease_file.write_text(
            "1717000000 AA:BB:CC:DD:EE:FF 192.168.100.10 dut1 *\n"
        )
        lease = dnsmasq.get_lease_by_mac(tmp_path, "aa:bb:cc:dd:ee:ff")
        assert lease is not None
        assert lease.ip == "192.168.100.10"

        assert dnsmasq.get_lease_by_mac(tmp_path, "00:00:00:00:00:00") is None


class TestStateDir:
    def test_state_dir_for_interface(self):
        from . import dnsmasq

        path = dnsmasq.state_dir_for_interface("enx00e04c683af1")
        assert path == Path("/var/lib/jumpstarter/dut-network-enx00e04c683af1")

    def test_state_dir_custom_base(self):
        from . import dnsmasq

        path = dnsmasq.state_dir_for_interface("eth0", base="/tmp/jmp")
        assert path == Path("/tmp/jmp/dut-network-eth0")


class TestEnsureStateDir:
    def test_creates_directory_with_correct_permissions(self, tmp_path: Path):
        from . import dnsmasq

        d = tmp_path / "new-state"
        dnsmasq.ensure_state_dir(d)
        assert d.is_dir()
        assert (d.stat().st_mode & 0o777) == 0o755

    def test_permission_error_falls_back_to_sudo(self, tmp_path: Path):
        from . import dnsmasq

        d = tmp_path / "root-owned"
        with patch.object(Path, "mkdir", side_effect=PermissionError("no perms")), \
             patch.object(Path, "chmod", side_effect=PermissionError("no perms")), \
             patch(f"{_DNSMASQ_MODULE}.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            dnsmasq.ensure_state_dir(d)
            assert mock_run.call_count == 3  # mkdir, chown, chmod


class TestDnsmasqStart:
    def test_raises_when_config_missing(self, tmp_path: Path):
        from . import dnsmasq

        with pytest.raises(FileNotFoundError, match="dnsmasq config not found"):
            dnsmasq.start(tmp_path)

    def test_raises_when_process_exits_immediately(self, tmp_path: Path):
        from . import dnsmasq

        conf = tmp_path / "dnsmasq.conf"
        conf.write_text("interface=br0\n")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.stderr.read.return_value = b"bind failed"
        with patch(f"{_DNSMASQ_MODULE}.subprocess.Popen", return_value=mock_proc), \
             patch(f"{_DNSMASQ_MODULE}.time.sleep"), \
             patch(f"{_DNSMASQ_MODULE}.time.monotonic", side_effect=[0.0, 3.0]):
            with pytest.raises(RuntimeError, match="dnsmasq failed to start"):
                dnsmasq.start(tmp_path)


    def test_raises_when_pidfile_not_created(self, tmp_path: Path):
        from . import dnsmasq

        conf = tmp_path / "dnsmasq.conf"
        conf.write_text("interface=br0\n")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = MagicMock()
        with patch(f"{_DNSMASQ_MODULE}.subprocess.Popen", return_value=mock_proc), \
             patch(f"{_DNSMASQ_MODULE}.time.sleep"), \
             patch(f"{_DNSMASQ_MODULE}.time.monotonic", side_effect=[0.0, 0.5, 3.0]):
            with pytest.raises(RuntimeError, match="did not create pidfile"):
                dnsmasq.start(tmp_path)
            mock_proc.terminate.assert_called_once()


class TestDnsmasqStop:
    def test_stop_via_process_handle(self):
        import signal

        from . import dnsmasq

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345
        with patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.stop(process=mock_proc)
            mock_signal.assert_called_once_with(12345, signal.SIGTERM)
            mock_proc.wait.assert_called_once_with(timeout=5)

    def test_stop_sigkill_fallback_on_timeout(self):
        import signal as sig

        from . import dnsmasq

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("dnsmasq", 5), None]
        with patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.stop(process=mock_proc)
            assert mock_signal.call_count == 2
            mock_signal.assert_any_call(12345, sig.SIGTERM)
            mock_signal.assert_any_call(12345, sig.SIGKILL)

    def test_stop_via_pidfile_only(self, tmp_path: Path):
        import signal as sig

        from . import dnsmasq

        pid_file = tmp_path / "dnsmasq.pid"
        pid_file.write_text("99999\n")
        with patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.stop(process=None, state_dir=tmp_path)
            mock_signal.assert_called_once_with(99999, sig.SIGTERM)

    def test_stop_prefers_pidfile_pid(self):
        import signal as sig

        from . import dnsmasq

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 11111
        with patch(f"{_DNSMASQ_MODULE}._read_pid_file", return_value=22222), \
             patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.stop(process=mock_proc, state_dir=Path("/fake"))
            mock_signal.assert_called_once_with(22222, sig.SIGTERM)


class TestReadPidFile:
    def test_reads_valid_pid(self, tmp_path: Path):
        from . import dnsmasq

        pid_file = tmp_path / "dnsmasq.pid"
        pid_file.write_text("42\n")
        assert dnsmasq._read_pid_file(tmp_path) == 42

    def test_returns_none_when_missing(self, tmp_path: Path):
        from . import dnsmasq

        assert dnsmasq._read_pid_file(tmp_path) is None

    def test_returns_none_on_invalid_content(self, tmp_path: Path):
        from . import dnsmasq

        pid_file = tmp_path / "dnsmasq.pid"
        pid_file.write_text("not-a-number\n")
        assert dnsmasq._read_pid_file(tmp_path) is None


class TestReloadConfig:
    def test_reload_via_process(self):
        import signal as sig

        from . import dnsmasq

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 12345
        with patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.reload_config(process=mock_proc)
            mock_signal.assert_called_once_with(12345, sig.SIGHUP)

    def test_reload_via_pidfile_only(self, tmp_path: Path):
        import signal as sig

        from . import dnsmasq

        pid_file = tmp_path / "dnsmasq.pid"
        pid_file.write_text("54321\n")
        with patch(f"{_DNSMASQ_MODULE}.signal_pid") as mock_signal:
            dnsmasq.reload_config(process=None, state_dir=tmp_path)
            mock_signal.assert_called_once_with(54321, sig.SIGHUP)


class TestCleanupStateDir:
    def test_removes_existing_directory(self, tmp_path: Path):
        from . import dnsmasq

        d = tmp_path / "state"
        d.mkdir()
        (d / "dnsmasq.conf").write_text("test")
        dnsmasq.cleanup_state_dir(d)
        assert not d.exists()

    def test_noop_when_directory_missing(self, tmp_path: Path):
        from . import dnsmasq

        d = tmp_path / "nonexistent"
        dnsmasq.cleanup_state_dir(d)  # should not raise


class TestUpdateConfig:
    def test_rewrites_and_reloads(self, tmp_path: Path):
        from . import dnsmasq

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        with patch(f"{_DNSMASQ_MODULE}.write_config") as mock_write, \
             patch(f"{_DNSMASQ_MODULE}.reload_config") as mock_reload:
            dnsmasq.update_config(
                state_dir=tmp_path, bridge="br0",
                range_start="10.0.0.100", range_end="10.0.0.200",
                static_leases=[], dns_servers=["8.8.8.8"],
                gateway_ip="10.0.0.1", process=mock_proc,
            )
            mock_write.assert_called_once()
            mock_reload.assert_called_once_with(process=mock_proc, state_dir=tmp_path)


class TestHostValidation:
    def test_invalid_ip_in_dns_entry(self, tmp_path: Path):
        from . import dnsmasq

        with pytest.raises(ValueError):
            dnsmasq.write_dns_hosts(tmp_path, [{"hostname": "ok.local", "ip": "not-an-ip"}])

    def test_invalid_hostname_in_dns_entry(self, tmp_path: Path):
        from . import dnsmasq

        with pytest.raises(ValueError, match="Invalid hostname"):
            dnsmasq.write_dns_hosts(tmp_path, [{"hostname": "bad host\nname", "ip": "10.0.0.1"}])

    def test_invalid_mac_in_dhcp_hosts(self, tmp_path: Path):
        from . import dnsmasq

        with pytest.raises(ValueError, match="Invalid MAC"):
            dnsmasq.write_dhcp_hosts(tmp_path, [{"mac": "not-a-mac", "ip": "10.0.0.1"}])

    def test_invalid_ip_in_dhcp_hosts(self, tmp_path: Path):
        from . import dnsmasq

        with pytest.raises(ValueError):
            dnsmasq.write_dhcp_hosts(tmp_path, [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "bad"}])


class TestWriteDhcpHosts:
    def test_writes_hosts_file(self, tmp_path: Path):
        from . import dnsmasq

        leases = [
            {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.10", "hostname": "dut1"},
            {"mac": "11:22:33:44:55:66", "ip": "10.0.0.11"},
        ]
        path = dnsmasq.write_dhcp_hosts(tmp_path, leases)
        content = path.read_text()
        assert "aa:bb:cc:dd:ee:ff,10.0.0.10,dut1" in content
        assert "11:22:33:44:55:66,10.0.0.11" in content

    def test_empty_leases(self, tmp_path: Path):
        from . import dnsmasq

        path = dnsmasq.write_dhcp_hosts(tmp_path, [])
        assert path.read_text() == ""
