from unittest.mock import patch

import pytest

from . import nftables


class TestMasqueradeRuleset:
    def test_ruleset_contains_expected_elements(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_masquerade_rules("br-jmp0", "eth0", "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "masquerade" in ruleset
            assert "br-jmp0" in ruleset
            assert "eth0" in ruleset
            assert "192.168.100.0/24" in ruleset
            assert "postrouting" in ruleset
            assert "forward" in ruleset

    def test_uses_custom_table_name(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_masquerade_rules("br-jmp0", "eth0", "192.168.100.0/24", table_name="my_table")
            ruleset = mock_load.call_args[0][0]
            assert "table ip my_table" in ruleset


class TestOneToOneRuleset:
    def test_single_mapping(self):
        mappings = [{"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"}]
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules("br-jmp0", "eth0", mappings, "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "dnat to 192.168.100.10" in ruleset
            assert "snat to 10.0.0.50" in ruleset
            assert "masquerade" in ruleset

    def test_multiple_mappings(self):
        mappings = [
            {"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"},
            {"private_ip": "192.168.100.11", "public_ip": "10.0.0.51"},
        ]
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules("br-jmp0", "eth0", mappings, "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "dnat to 192.168.100.10" in ruleset
            assert "dnat to 192.168.100.11" in ruleset
            assert "snat to 10.0.0.50" in ruleset
            assert "snat to 10.0.0.51" in ruleset
            assert ruleset.count("dnat to") == 2
            assert ruleset.count("snat to") == 2

    def test_empty_mappings(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules("br-jmp0", "eth0", [], "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "masquerade" in ruleset
            assert "dnat to" not in ruleset


class TestInterfaceNameValidation:
    def test_rejects_invalid_names(self):
        with pytest.raises(ValueError, match="Invalid interface name"):
            nftables.apply_masquerade_rules("br jmp; drop", "eth0", "192.168.0.0/24")

    def test_rejects_too_long_names(self):
        with pytest.raises(ValueError, match="Invalid interface name"):
            nftables.apply_masquerade_rules("a" * 16, "eth0", "192.168.0.0/24")

    def test_accepts_valid_names(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset"):
            nftables.apply_masquerade_rules("br-jmp.0", "eth_0", "192.168.0.0/24")


class TestTableNameFor:
    def test_replaces_hyphens(self):
        assert nftables._table_name_for("br-jmp-eth0") == "jumpstarter_br_jmp_eth0"

    def test_preserves_underscores(self):
        assert nftables._table_name_for("br_test") == "jumpstarter_br_test"


class TestFlushRules:
    def test_flushes_specific_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules("my_custom_table")
            mock.assert_called_once_with(["delete", "table", "ip", "my_custom_table"], check=False)

    def test_flushes_default_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules()
            mock.assert_called_once_with(["delete", "table", "ip", "jumpstarter"], check=False)
