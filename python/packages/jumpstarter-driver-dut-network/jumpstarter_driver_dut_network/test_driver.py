import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

    from .driver import DutNetwork

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

    return driver, mock_iproute, mock_nftables, mock_dnsmasq


class TestDriverValidation:
    def test_gateway_outside_subnet_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not within subnet"):
            _make_driver(tmp_path, gateway_ip="10.0.0.1", subnet="192.168.100.0/24")

    def test_1to1_without_public_ip_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="public_ip"):
            _make_driver(
                tmp_path,
                nat_mode="1to1",
                addresses=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10"}],
            )

    def test_1to1_with_public_ip_ok(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(
            tmp_path,
            nat_mode="1to1",
            addresses=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10", "public_ip": "10.0.0.50"}],
        )
        assert driver.nat_mode == "1to1"

    def test_valid_masquerade_config(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        assert driver._prefix_len == 24


class TestTransactionalSetup:
    def test_cleanup_called_on_setup_failure(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Cannot detect upstream"):
            with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
                 patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
                 patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
                 patch(f"{_DRIVER_MODULE}.nftables") as mock_nft, \
                 patch(f"{_DRIVER_MODULE}.dnsmasq"):
                mock_sys.platform = "linux"
                mock_shutil.which.return_value = "/usr/bin/fake"
                mock_iproute.interface_exists.return_value = False
                mock_iproute.detect_upstream_interface.return_value = None
                mock_nft._table_name_for.return_value = "jumpstarter_eth0"
                from .driver import DutNetwork
                DutNetwork(
                    interface="eth0",
                    subnet="192.168.100.0/24",
                    gateway_ip="192.168.100.1",
                    upstream_interface=None,
                    nat_mode="masquerade",
                    state_dir=str(tmp_path),
                )  # type: ignore[missing-argument]


class TestDriverSetupMasquerade:
    def test_calls_configure_and_nat(self, tmp_path: Path):
        _, mock_ip, mock_nft, mock_dns = _make_driver(tmp_path, nat_mode="masquerade")
        mock_ip.nm_set_unmanaged.assert_called_once_with("eth-dut")
        mock_ip.configure_interface.assert_called_once_with("eth-dut", "192.168.100.1", 24)
        mock_ip.set_interface_forwarding.assert_any_call("eth-dut", True)
        mock_ip.set_interface_forwarding.assert_any_call("eth-up", True)
        mock_nft.apply_masquerade_rules.assert_called_once_with(
            "eth-dut", "eth-up", "192.168.100.0/24",
            table_name="jumpstarter_eth_dut",
        )
        mock_dns.write_config.assert_called_once()
        mock_dns.start.assert_called_once()

    def test_saves_previous_forwarding_per_interface(self, tmp_path: Path):
        driver, mock_ip, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        assert mock_ip.get_interface_forwarding.call_count == 2

    def test_calls_ensure_filter_forward(self, tmp_path: Path):
        _, _, mock_nft, _ = _make_driver(tmp_path, nat_mode="masquerade")
        mock_nft.ensure_filter_forward.assert_called_once_with("eth-dut", "eth-up")


class TestDriverSetup1to1:
    def test_creates_aliases_and_rules(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="1to1", addresses=leases)
        mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.50", 24)
        mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.51", 24)
        assert mock_ip.add_ip_alias.call_count == 2
        expected_mappings = [
            {"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"private_ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        mock_nft.apply_1to1_rules.assert_called_once_with(
            "eth-dut", "eth-up", expected_mappings, "192.168.100.0/24",
            table_name="jumpstarter_eth_dut",
        )

    def test_skips_lease_without_public_ip(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11"},
        ]
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="1to1", addresses=leases)
        assert mock_ip.add_ip_alias.call_count == 1
        mappings = mock_nft.apply_1to1_rules.call_args[0][2]
        assert len(mappings) == 1


class TestDriverSetupDisabled:
    def test_skips_forwarding_and_nat(self, tmp_path: Path):
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="disabled")
        mock_ip.set_interface_forwarding.assert_not_called()
        mock_nft.ensure_filter_forward.assert_not_called()
        mock_nft.apply_masquerade_rules.assert_not_called()
        mock_nft.apply_1to1_rules.assert_not_called()

    def test_none_alias_same_as_disabled(self, tmp_path: Path):
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="none")
        mock_ip.set_interface_forwarding.assert_not_called()
        mock_nft.apply_masquerade_rules.assert_not_called()

    def test_interface_still_configured(self, tmp_path: Path):
        _, mock_ip, _, _ = _make_driver(tmp_path, nat_mode="disabled")
        mock_ip.configure_interface.assert_called_once()

    def test_upstream_not_required(self, tmp_path: Path):
        driver, mock_ip, _, _ = _make_driver(
            tmp_path, nat_mode="disabled", upstream_interface=None,
        )
        mock_ip.detect_upstream_interface.assert_not_called()


class TestDriverCleanup:
    def test_cleanup_masquerade(self, tmp_path: Path):
        driver, mock_ip, mock_nft, mock_dns = _make_driver(tmp_path, nat_mode="masquerade")
        with patch(f"{_DRIVER_MODULE}.iproute") as mock_ip2, \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft2, \
             patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dns2:
            driver.cleanup()
            mock_nft2.flush_rules.assert_called_once_with(driver._table_name)
            mock_ip2.deconfigure_interface.assert_called_once_with("eth-dut")
            mock_ip2.nm_set_managed.assert_called_once_with("eth-dut")
            mock_dns2.stop.assert_called_once()

    def test_cleanup_1to1_removes_aliases(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="1to1", addresses=leases)
        assert driver._added_aliases == {"10.0.0.50", "10.0.0.51"}
        with patch(f"{_DRIVER_MODULE}.iproute") as mock_ip2, \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_ip2.remove_ip_alias.assert_any_call("eth-up", "10.0.0.50", 24)
            mock_ip2.remove_ip_alias.assert_any_call("eth-up", "10.0.0.51", 24)
            assert mock_ip2.remove_ip_alias.call_count == 2
        assert driver._added_aliases == set()

    def test_cleanup_restores_forwarding_per_interface(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        with patch(f"{_DRIVER_MODULE}.iproute") as mock_ip2, \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_ip2.set_interface_forwarding.assert_any_call("eth-dut", False)
            mock_ip2.set_interface_forwarding.assert_any_call("eth-up", False)

    def test_cleanup_removes_filter_forward_rules(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        driver._fwd_rule_handles = [42, 43]
        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft2, \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_nft2.remove_filter_forward.assert_called_once_with([42, 43])
        assert driver._fwd_rule_handles == []

    def test_cleanup_skips_filter_forward_when_no_handles(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        assert driver._fwd_rule_handles == []
        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft2, \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_nft2.remove_filter_forward.assert_not_called()

    def test_cleanup_removes_state_directory(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dns2:
            driver.cleanup()
            mock_dns2.cleanup_state_dir.assert_called_once_with(tmp_path)


class TestDriverDnsEntries:
    def test_dns_entries_passed_to_dnsmasq(self, tmp_path: Path):
        entries = [{"hostname": "foo.local", "ip": "10.0.0.1"}]
        _, _, _, mock_dns = _make_driver(tmp_path, dns_entries=entries)
        write_call = mock_dns.write_config.call_args
        assert write_call.kwargs.get("dns_entries") == entries or \
            any(a == entries for a in write_call.args)

    def test_get_dns_entries(self, tmp_path: Path):
        entries = [{"hostname": "a.local", "ip": "1.2.3.4"}]
        driver, _, _, _ = _make_driver(tmp_path, dns_entries=entries)
        assert driver.get_dns_entries() == entries

    def test_add_dns_entry(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dns:
            driver.add_dns_entry("new.local", "5.6.7.8")
            assert {"hostname": "new.local", "ip": "5.6.7.8"} in driver.dns_entries
            mock_dns.write_config.assert_called_once()
            mock_dns.reload_config.assert_called_once()

    def test_add_replaces_existing_hostname(self, tmp_path: Path):
        entries = [{"hostname": "a.local", "ip": "1.1.1.1"}]
        driver, _, _, _ = _make_driver(tmp_path, dns_entries=entries)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_dns_entry("a.local", "2.2.2.2")
            assert len(driver.dns_entries) == 1
            assert driver.dns_entries[0]["ip"] == "2.2.2.2"

    def test_remove_dns_entry(self, tmp_path: Path):
        entries = [{"hostname": "a.local", "ip": "1.1.1.1"}]
        driver, _, _, _ = _make_driver(tmp_path, dns_entries=entries)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.remove_dns_entry("a.local")
            assert driver.dns_entries == []


class TestDriverAddresses:
    def test_add_address_with_mac(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_address("192.168.100.50", mac="aa:bb:cc:dd:ee:ff", hostname="new-dut")
            assert any(entry["mac"] == "aa:bb:cc:dd:ee:ff" for entry in driver.addresses)

    def test_add_address_without_mac(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_address("192.168.100.50", hostname="nat-only", public_ip="10.0.0.50")
            entry = driver.addresses[0]
            assert "mac" not in entry
            assert entry["public_ip"] == "10.0.0.50"

    def test_add_address_with_public_ip(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_address("192.168.100.50", mac="aa:bb:cc:dd:ee:ff", hostname="dut", public_ip="10.0.0.50")
            entry = driver.addresses[0]
            assert entry["public_ip"] == "10.0.0.50"

    def test_add_replaces_existing_ip(self, tmp_path: Path):
        addrs = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.100.10"}]
        driver, _, _, _ = _make_driver(tmp_path, addresses=addrs)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_address("192.168.100.10", mac="11:22:33:44:55:66")
            assert len(driver.addresses) == 1
            assert driver.addresses[0]["mac"] == "11:22:33:44:55:66"

    def test_remove_address(self, tmp_path: Path):
        addrs = [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10"}]
        driver, _, _, _ = _make_driver(tmp_path, addresses=addrs)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.remove_address("192.168.100.10")
            assert driver.addresses == []


class TestGet1to1Mappings:
    def test_extracts_mappings(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11"},
            {"mac": "aa:bb:cc:dd:ee:03", "ip": "192.168.100.12", "public_ip": "10.0.0.52"},
        ]
        driver, _, _, _ = _make_driver(
            tmp_path, nat_mode="1to1", addresses=leases,
        )
        mappings = driver._get_1to1_mappings()
        assert len(mappings) == 2
        assert {"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"} in mappings
        assert {"private_ip": "192.168.100.12", "public_ip": "10.0.0.52"} in mappings


class TestResolveIp:
    """Tests for DutNetwork._resolve_ip() DNS resolution helper."""

    def test_valid_ipv4_returned_unchanged(self):
        from .driver import DutNetwork

        assert DutNetwork._resolve_ip("10.0.0.50") == "10.0.0.50"
        assert DutNetwork._resolve_ip("192.168.1.1") == "192.168.1.1"
        assert DutNetwork._resolve_ip("255.255.255.255") == "255.255.255.255"

    def test_hostname_resolved_to_ip(self):
        from .driver import DutNetwork

        fake_result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.99", 0))]
        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", return_value=fake_result):
            assert DutNetwork._resolve_ip("myhost.example.com") == "10.0.0.99"

    def test_unresolvable_hostname_raises(self):
        from .driver import DutNetwork

        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            with pytest.raises(ValueError, match="Cannot resolve hostname"):
                DutNetwork._resolve_ip("no-such-host.invalid")

    def test_empty_getaddrinfo_result_raises(self):
        from .driver import DutNetwork

        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", return_value=[]):
            with pytest.raises(ValueError, match="Cannot resolve hostname"):
                DutNetwork._resolve_ip("empty-result.invalid")


class TestDnsNameIn1to1:
    """Integration tests: DNS hostnames in public_ip with 1:1 NAT setup."""

    def test_hostname_public_ip_resolved_during_setup(self, tmp_path: Path):
        fake_result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.99", 0))]
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "myhost.example.com"},
        ]
        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", return_value=fake_result) as mock_gai:
            driver, mock_ip, mock_nft, _ = _make_driver(
                tmp_path, nat_mode="1to1", addresses=leases,
            )
            mock_gai.assert_called_once_with("myhost.example.com", None, socket.AF_INET, socket.SOCK_STREAM)
            mock_ip.add_ip_alias.assert_called_once_with("eth-up", "10.0.0.99", 24)
            expected_mappings = [{"private_ip": "192.168.100.10", "public_ip": "10.0.0.99"}]
            mock_nft.apply_1to1_rules.assert_called_once_with(
                "eth-dut", "eth-up", expected_mappings, "192.168.100.0/24",
                table_name="jumpstarter_eth_dut",
            )
            assert "10.0.0.99" in driver._added_aliases

    def test_mixed_ip_and_hostname(self, tmp_path: Path):
        fake_result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.99", 0))]
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11", "public_ip": "myhost.example.com"},
        ]
        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", return_value=fake_result):
            driver, mock_ip, mock_nft, _ = _make_driver(
                tmp_path, nat_mode="1to1", addresses=leases,
            )
            assert mock_ip.add_ip_alias.call_count == 2
            mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.50", 24)
            mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.99", 24)

    def test_unresolvable_hostname_raises_during_setup(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "bad-host.invalid"},
        ]
        with patch(f"{_DRIVER_MODULE}.socket.getaddrinfo", side_effect=socket.gaierror("fail")):
            with pytest.raises(ValueError, match="Cannot resolve hostname"):
                _make_driver(tmp_path, nat_mode="1to1", addresses=leases)
