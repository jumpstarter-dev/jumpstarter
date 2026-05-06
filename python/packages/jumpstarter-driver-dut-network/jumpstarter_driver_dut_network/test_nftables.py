import subprocess
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
            assert ruleset.count("dnat to") == 4  # 2x prerouting + 2x output
            assert ruleset.count("snat to") == 2

    def test_empty_mappings(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules("br-jmp0", "eth0", [], "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "masquerade" in ruleset
            assert "dnat to" not in ruleset

    def test_output_chain_for_hairpin_nat(self):
        """The output chain enables the exporter host to reach DUTs via public IPs."""
        mappings = [{"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"}]
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules("br-jmp0", "eth0", mappings, "192.168.100.0/24")
            ruleset = mock_load.call_args[0][0]
            assert "chain output" in ruleset
            assert "type nat hook output priority dstnat" in ruleset
            assert "ip daddr 10.0.0.50 dnat to 192.168.100.10" in ruleset


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


class TestSubnetValidation:
    def test_rejects_invalid_subnet(self):
        with pytest.raises(ValueError):
            nftables.apply_masquerade_rules("br0", "eth0", "not-a-subnet")

    def test_rejects_invalid_ip_in_mapping(self):
        mappings = [{"private_ip": "not-an-ip", "public_ip": "10.0.0.1"}]
        with pytest.raises(ValueError):
            nftables.apply_1to1_rules("br0", "eth0", mappings, "192.168.0.0/24")


class TestTableNameFor:
    def test_replaces_hyphens(self):
        assert nftables._table_name_for("br-jmp-eth0") == "jumpstarter_br_jmp_eth0"

    def test_preserves_underscores(self):
        assert nftables._table_name_for("br_test") == "jumpstarter_br_test"


class TestFilterForwardDrop:
    def test_detects_policy_drop(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='table ip filter {\n  chain FORWARD {\n    policy drop;\n  }\n}\n',
        )
        with patch.object(nftables, "_run_nft", return_value=fake):
            assert nftables.is_filter_forward_drop() is True

    def test_returns_false_for_policy_accept(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='table ip filter {\n  chain FORWARD {\n    policy accept;\n  }\n}\n',
        )
        with patch.object(nftables, "_run_nft", return_value=fake):
            assert nftables.is_filter_forward_drop() is False

    def test_returns_false_when_table_missing(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with patch.object(nftables, "_run_nft", return_value=fake):
            assert nftables.is_filter_forward_drop() is False

    def test_ensure_inserts_rules_when_drop(self):
        insert_output = 'insert rule ip filter FORWARD iifname "br-jmp0" accept # handle 42\n'
        fake_insert = subprocess.CompletedProcess(args=[], returncode=0, stdout=insert_output)
        with patch.object(nftables, "is_filter_forward_drop", return_value=True), \
             patch.object(nftables, "_run_nft", return_value=fake_insert) as mock_nft:
            handles = nftables.ensure_filter_forward("br-jmp0", "eth0")
            assert len(handles) == 4
            assert all(h == 42 for h in handles)
            assert mock_nft.call_count == 4

    def test_ensure_returns_empty_when_accept(self):
        with patch.object(nftables, "is_filter_forward_drop", return_value=False):
            handles = nftables.ensure_filter_forward("br-jmp0", "eth0")
            assert handles == []

    def test_ensure_handles_insert_failure(self):
        fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch.object(nftables, "is_filter_forward_drop", return_value=True), \
             patch.object(nftables, "_run_nft", return_value=fail):
            handles = nftables.ensure_filter_forward("br-jmp0", "eth0")
            assert handles == []

    def test_remove_deletes_by_handle(self):
        with patch.object(nftables, "_run_nft") as mock_nft:
            nftables.remove_filter_forward([42, 43])
            assert mock_nft.call_count == 2
            mock_nft.assert_any_call(
                ["delete", "rule", "ip", "filter", "FORWARD", "handle", "42"],
                check=False,
            )
            mock_nft.assert_any_call(
                ["delete", "rule", "ip", "filter", "FORWARD", "handle", "43"],
                check=False,
            )

    def test_remove_noop_with_empty_handles(self):
        with patch.object(nftables, "_run_nft") as mock_nft:
            nftables.remove_filter_forward([])
            mock_nft.assert_not_called()


class TestFlushRules:
    def test_flushes_specific_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules("my_custom_table")
            mock.assert_called_once_with(["delete", "table", "ip", "my_custom_table"], check=False)

    def test_flushes_default_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules()
            mock.assert_called_once_with(["delete", "table", "ip", "jumpstarter"], check=False)
