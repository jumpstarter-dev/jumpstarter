from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_DRIVER_MODULE = "jumpstarter_driver_dut_network.driver"


def _make_driver(tmp_path, **overrides):
    """Create a DutNetwork driver with all system calls mocked."""
    params = {
        "interface": "eth-dut",
        "bridge_name": "br-test",
        "subnet": "192.168.100.0/24",
        "gateway_ip": "192.168.100.1",
        "upstream_interface": "eth-up",
        "nat_mode": "masquerade",
        "dhcp_enabled": True,
        "dhcp_range_start": "192.168.100.100",
        "dhcp_range_end": "192.168.100.200",
        "static_leases": [],
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
        mock_iproute.get_bridge_slaves.return_value = []
        mock_iproute.get_interface_addresses.return_value = []
        mock_iproute.get_interface_forwarding.return_value = "0"
        mock_iproute.get_interface_prefix_len.return_value = 24
        mock_iproute.ensure_iptables_forward.return_value = []
        mock_nftables.list_rules.return_value = ""
        mock_nftables._table_name_for.return_value = "jumpstarter_br_test"
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
                static_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10"}],
            )

    def test_1to1_with_public_ip_ok(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(
            tmp_path,
            nat_mode="1to1",
            static_leases=[{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10", "public_ip": "10.0.0.50"}],
        )
        assert driver.nat_mode == "1to1"

    def test_valid_masquerade_config(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        assert driver._prefix_len == 24


class TestBridgeNameDerivation:
    def test_auto_derives_from_interface(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, bridge_name=None, interface="eth0")
        assert driver.bridge_name == "br-jmp-eth0"

    def test_truncates_to_15_chars(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, bridge_name=None, interface="enx00e04c683af1")
        assert len(driver.bridge_name) <= 15
        assert driver.bridge_name == "br-jmp-enx00e04"

    def test_explicit_bridge_name_preserved(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, bridge_name="my-bridge")
        assert driver.bridge_name == "my-bridge"

    def test_collision_with_different_slave_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="already exists with different slaves"):
            with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
                 patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
                 patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
                 patch(f"{_DRIVER_MODULE}.nftables"), \
                 patch(f"{_DRIVER_MODULE}.dnsmasq"):
                mock_sys.platform = "linux"
                mock_shutil.which.return_value = "/usr/bin/fake"
                mock_iproute.interface_exists.return_value = True
                mock_iproute.get_bridge_slaves.return_value = ["other-iface"]
                from .driver import DutNetwork
                DutNetwork(
                    interface="eth0",
                    bridge_name=None,
                    subnet="192.168.100.0/24",
                    gateway_ip="192.168.100.1",
                    upstream_interface="eth-up",
                    state_dir=str(tmp_path),
                )  # type: ignore[missing-argument]

    def test_restart_with_same_slave_succeeds(self, tmp_path: Path):
        with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
             patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
             patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft, \
             patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dns:
            mock_sys.platform = "linux"
            mock_shutil.which.return_value = "/usr/bin/fake"
            mock_iproute.interface_exists.return_value = True
            mock_iproute.get_bridge_slaves.return_value = ["eth0"]
            mock_iproute.detect_upstream_interface.return_value = "eth-up"
            mock_iproute.get_interface_forwarding.return_value = "0"
            mock_iproute.get_interface_prefix_len.return_value = 24
            mock_iproute.get_interface_addresses.return_value = []
            mock_nft._table_name_for.return_value = "jumpstarter_br_jmp_eth0"
            mock_nft.list_rules.return_value = ""
            mock_dns.state_dir_for_interface.return_value = tmp_path
            mock_dns.start.return_value = MagicMock()
            from .driver import DutNetwork
            driver = DutNetwork(
                interface="eth0",
                bridge_name=None,
                subnet="192.168.100.0/24",
                gateway_ip="192.168.100.1",
                upstream_interface="eth-up",
                state_dir=str(tmp_path),
            )  # type: ignore[missing-argument]
            assert driver.bridge_name == "br-jmp-eth0"


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
                mock_nft._table_name_for.return_value = "jumpstarter_br_test"
                from .driver import DutNetwork
                DutNetwork(
                    interface="eth0",
                    bridge_name="br-test",
                    subnet="192.168.100.0/24",
                    gateway_ip="192.168.100.1",
                    upstream_interface=None,
                    nat_mode="masquerade",
                    state_dir=str(tmp_path),
                )  # type: ignore[missing-argument]


class TestDriverSetupMasquerade:
    def test_calls_bridge_and_nat(self, tmp_path: Path):
        _, mock_ip, mock_nft, mock_dns = _make_driver(tmp_path, nat_mode="masquerade")
        mock_ip.nm_set_unmanaged.assert_called_once_with("eth-dut")
        mock_ip.create_bridge.assert_called_once_with("br-test", "192.168.100.1", 24)
        mock_ip.add_slave.assert_called_once_with("br-test", "eth-dut")
        mock_ip.set_interface_forwarding.assert_any_call("br-test", True)
        mock_ip.set_interface_forwarding.assert_any_call("eth-up", True)
        mock_nft.apply_masquerade_rules.assert_called_once_with(
            "br-test", "eth-up", "192.168.100.0/24",
            table_name="jumpstarter_br_test",
        )
        mock_dns.write_config.assert_called_once()
        mock_dns.start.assert_called_once()

    def test_saves_previous_forwarding_per_interface(self, tmp_path: Path):
        driver, mock_ip, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        assert mock_ip.get_interface_forwarding.call_count == 2

    def test_calls_ensure_iptables_forward(self, tmp_path: Path):
        _, mock_ip, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        mock_ip.ensure_iptables_forward.assert_called_once_with("br-test", "eth-up")


class TestDriverSetup1to1:
    def test_creates_aliases_and_rules(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="1to1", static_leases=leases)
        mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.50", 24)
        mock_ip.add_ip_alias.assert_any_call("eth-up", "10.0.0.51", 24)
        assert mock_ip.add_ip_alias.call_count == 2
        expected_mappings = [
            {"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"private_ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        mock_nft.apply_1to1_rules.assert_called_once_with(
            "br-test", "eth-up", expected_mappings, "192.168.100.0/24",
            table_name="jumpstarter_br_test",
        )

    def test_skips_lease_without_public_ip(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11"},
        ]
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="1to1", static_leases=leases)
        assert mock_ip.add_ip_alias.call_count == 1
        mappings = mock_nft.apply_1to1_rules.call_args[0][2]
        assert len(mappings) == 1


class TestDriverSetupDisabled:
    def test_skips_forwarding_and_nat(self, tmp_path: Path):
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="disabled")
        mock_ip.set_interface_forwarding.assert_not_called()
        mock_ip.ensure_iptables_forward.assert_not_called()
        mock_nft.apply_masquerade_rules.assert_not_called()
        mock_nft.apply_1to1_rules.assert_not_called()

    def test_none_alias_same_as_disabled(self, tmp_path: Path):
        _, mock_ip, mock_nft, _ = _make_driver(tmp_path, nat_mode="none")
        mock_ip.set_interface_forwarding.assert_not_called()
        mock_nft.apply_masquerade_rules.assert_not_called()

    def test_bridge_still_created(self, tmp_path: Path):
        _, mock_ip, _, _ = _make_driver(tmp_path, nat_mode="disabled")
        mock_ip.create_bridge.assert_called_once()
        mock_ip.add_slave.assert_called_once()

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
            mock_ip2.delete_bridge.assert_called_once_with("br-test")
            mock_ip2.nm_set_managed.assert_called_once_with("eth-dut")
            mock_dns2.stop.assert_called_once()

    def test_cleanup_1to1_removes_aliases(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="1to1", static_leases=leases)
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
            mock_ip2.set_interface_forwarding.assert_any_call("br-test", False)
            mock_ip2.set_interface_forwarding.assert_any_call("eth-up", False)

    def test_cleanup_removes_iptables_rules(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        fake_rules = [("-i", "br-test"), ("-o", "br-test")]
        driver._iptables_rules = fake_rules
        with patch(f"{_DRIVER_MODULE}.iproute") as mock_ip2, \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_ip2.remove_iptables_forward.assert_called_once_with(fake_rules)
        assert driver._iptables_rules == []

    def test_cleanup_skips_iptables_when_no_rules(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path, nat_mode="masquerade")
        assert driver._iptables_rules == []
        with patch(f"{_DRIVER_MODULE}.iproute") as mock_ip2, \
             patch(f"{_DRIVER_MODULE}.nftables"), \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_ip2.remove_iptables_forward.assert_not_called()


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


class TestDriverStaticLeases:
    def test_add_static_lease(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_static_lease("aa:bb:cc:dd:ee:ff", "192.168.100.50", "new-dut")
            assert any(lease["mac"] == "aa:bb:cc:dd:ee:ff" for lease in driver.static_leases)

    def test_add_lease_with_public_ip(self, tmp_path: Path):
        driver, _, _, _ = _make_driver(tmp_path)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_static_lease("aa:bb:cc:dd:ee:ff", "192.168.100.50", "dut", "10.0.0.50")
            lease = driver.static_leases[0]
            assert lease["public_ip"] == "10.0.0.50"

    def test_add_replaces_existing_mac(self, tmp_path: Path):
        leases = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.100.10"}]
        driver, _, _, _ = _make_driver(tmp_path, static_leases=leases)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.add_static_lease("aa:bb:cc:dd:ee:ff", "192.168.100.50")
            assert len(driver.static_leases) == 1
            assert driver.static_leases[0]["ip"] == "192.168.100.50"

    def test_remove_static_lease(self, tmp_path: Path):
        leases = [{"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.100.10"}]
        driver, _, _, _ = _make_driver(tmp_path, static_leases=leases)
        with patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.remove_static_lease("aa:bb:cc:dd:ee:ff")
            assert driver.static_leases == []


class TestGet1to1Mappings:
    def test_extracts_mappings(self, tmp_path: Path):
        leases = [
            {"mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.100.11"},
            {"mac": "aa:bb:cc:dd:ee:03", "ip": "192.168.100.12", "public_ip": "10.0.0.52"},
        ]
        driver, _, _, _ = _make_driver(
            tmp_path, nat_mode="1to1", static_leases=leases,
        )
        mappings = driver._get_1to1_mappings()
        assert len(mappings) == 2
        assert {"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"} in mappings
        assert {"private_ip": "192.168.100.12", "public_ip": "10.0.0.52"} in mappings
