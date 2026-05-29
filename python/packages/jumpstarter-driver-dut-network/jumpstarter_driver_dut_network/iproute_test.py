import subprocess
from unittest.mock import patch

from . import iproute


class TestDetectUpstreamInterface:
    def test_parses_default_route(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="default via 10.0.0.1 dev eth0 proto static\n"
        )
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.detect_upstream_interface() == "eth0"

    def test_returns_none_on_failure(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.detect_upstream_interface() is None

    def test_returns_none_on_missing_dev(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="unreachable default\n")
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.detect_upstream_interface() is None


class TestGetInterfaceAddresses:
    def test_parses_addresses(self):
        output = "2: eth0    inet 192.168.100.1/24 brd 192.168.100.255 scope global eth0\n"
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=output)
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.get_interface_addresses("eth0") == ["192.168.100.1/24"]


class TestGetInterfacePrefixLen:
    def test_returns_prefix_len(self):
        output = "2: eth0    inet 10.99.0.2/24 brd 10.99.0.255 scope global eth0\n"
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=output)
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.get_interface_prefix_len("eth0") == 24

    def test_returns_none_when_no_addresses(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.get_interface_prefix_len("eth0") is None


class TestConfigureInterface:
    def test_calls_correct_commands(self):
        with patch.object(iproute, "_run_priv") as mock:
            iproute.configure_interface("eth0", "10.0.0.1", 24)
            mock.assert_any_call(["ip", "addr", "flush", "dev", "eth0"])
            mock.assert_any_call(["ip", "addr", "add", "10.0.0.1/24", "dev", "eth0"])
            mock.assert_any_call(["ip", "link", "set", "eth0", "up"])
            assert mock.call_count == 3


class TestDeconfigureInterface:
    def test_calls_correct_commands(self):
        with patch.object(iproute, "_run_priv") as mock:
            iproute.deconfigure_interface("eth0")
            mock.assert_any_call(["ip", "addr", "flush", "dev", "eth0"], check=False)
            mock.assert_any_call(["ip", "link", "set", "eth0", "down"], check=False)
            assert mock.call_count == 2


class TestAddIpAlias:
    def test_adds_ip_when_not_present(self):
        with patch.object(iproute, "get_interface_addresses", return_value=[]), \
             patch.object(iproute, "_run_priv") as mock:
            iproute.add_ip_alias("eth0", "10.0.0.2", 24)
            mock.assert_called_once_with(["ip", "addr", "add", "10.0.0.2/24", "dev", "eth0"])

    def test_skips_add_when_already_present(self):
        with patch.object(iproute, "get_interface_addresses", return_value=["10.0.0.2/24"]), \
             patch.object(iproute, "_run_priv") as mock:
            iproute.add_ip_alias("eth0", "10.0.0.2", 24)
            mock.assert_not_called()


class TestNetworkManagerAwareness:
    def test_nm_set_unmanaged_skips_when_nm_absent(self):
        with patch.object(iproute, "is_nm_running", return_value=False), \
             patch.object(iproute, "_run_priv") as mock:
            iproute.nm_set_unmanaged("eth0")
            mock.assert_not_called()

    def test_nm_set_unmanaged_calls_nmcli_when_present(self):
        with patch.object(iproute, "is_nm_running", return_value=True), \
             patch.object(iproute, "_run_priv") as mock:
            iproute.nm_set_unmanaged("eth0")
            mock.assert_called_once_with(
                ["nmcli", "device", "set", "eth0", "managed", "no"], check=False
            )


class TestGetInterfaceForwarding:
    def test_returns_current_value(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="1\n")
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.get_interface_forwarding("eth0") == "1"

    def test_returns_zero_on_failure(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with patch.object(iproute, "_run", return_value=fake):
            assert iproute.get_interface_forwarding("eth0") == "0"

    def test_uses_correct_sysctl_key(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="0\n")
        with patch.object(iproute, "_run", return_value=fake) as mock_run:
            iproute.get_interface_forwarding("eth0")
            mock_run.assert_called_once_with(
                ["sysctl", "-n", "net.ipv4.conf.eth0.forwarding"], check=False
            )

    def test_set_interface_forwarding(self):
        with patch.object(iproute, "_run_priv") as mock:
            iproute.set_interface_forwarding("eth0", True)
            mock.assert_called_once_with(
                ["sysctl", "-w", "net.ipv4.conf.eth0.forwarding=1"]
            )
