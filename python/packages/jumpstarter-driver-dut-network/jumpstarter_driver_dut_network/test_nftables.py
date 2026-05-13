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


class TestBuildForwardChain:
    """Tests for _build_forward_chain() rule generation."""

    def test_no_filter_defaults_to_accept(self):
        chain = nftables._build_forward_chain("dutif", "upstream")
        assert "ct state related,established accept" in chain
        assert 'iifname "dutif" oifname "upstream" accept' in chain
        assert 'iifname "upstream" oifname "dutif" accept' in chain

    def test_egress_drop_rule(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [
                    {"action": "drop", "destination": "10.0.0.0/8"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert 'iifname "dutif" oifname "upstream" ip daddr 10.0.0.0/8 drop' in chain
        # Egress catch-all should be accept
        lines = chain.splitlines()
        egress_catchall = [
            line for line in lines
            if 'iifname "dutif" oifname "upstream" accept' in line.strip()
        ]
        assert len(egress_catchall) == 1

    def test_egress_rule_with_port(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [
                    {"action": "drop", "destination": "0.0.0.0/0", "port": 25, "protocol": "tcp"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert "ip daddr 0.0.0.0/0 tcp dport 25 drop" in chain

    def test_ingress_accept_rule(self):
        cfg = {
            "ingress": {
                "policy": "drop",
                "rules": [
                    {"action": "accept", "source": "10.26.28.0/24", "port": 22, "protocol": "tcp"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert 'iifname "upstream" oifname "dutif" ip saddr 10.26.28.0/24 tcp dport 22 accept' in chain
        # Ingress catch-all should be drop
        lines = chain.splitlines()
        ingress_catchall = [
            line for line in lines
            if '"upstream"' in line and '"dutif"' in line and line.strip().endswith("drop")
            and "saddr" not in line
        ]
        assert len(ingress_catchall) == 1

    def test_egress_policy_drop(self):
        cfg = {
            "egress": {
                "policy": "drop",
                "rules": [],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        lines = chain.splitlines()
        egress_catchall = [
            line for line in lines
            if '"dutif"' in line and '"upstream"' in line and line.strip().endswith("drop")
            and "daddr" not in line
        ]
        assert len(egress_catchall) == 1

    def test_ingress_policy_accept(self):
        cfg = {
            "ingress": {
                "policy": "accept",
                "rules": [],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        lines = chain.splitlines()
        ingress_catchall = [
            line for line in lines
            if 'iifname "upstream" oifname "dutif" accept' in line.strip()
        ]
        assert len(ingress_catchall) == 1

    def test_conntrack_first(self):
        """ct state related,established must appear before any filter rules."""
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [{"action": "drop", "destination": "10.0.0.0/8"}],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        ct_pos = chain.index("ct state related,established accept")
        rule_pos = chain.index("ip daddr 10.0.0.0/8 drop")
        assert ct_pos < rule_pos

    def test_egress_before_ingress(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [{"action": "drop", "destination": "10.0.0.0/8"}],
            },
            "ingress": {
                "policy": "drop",
                "rules": [{"action": "accept", "source": "192.168.0.0/16"}],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        egress_pos = chain.index("ip daddr 10.0.0.0/8")
        ingress_pos = chain.index("ip saddr 192.168.0.0/16")
        assert egress_pos < ingress_pos

    def test_multiple_egress_rules(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [
                    {"action": "drop", "destination": "10.0.0.0/8"},
                    {"action": "drop", "destination": "172.16.0.0/12"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert "ip daddr 10.0.0.0/8 drop" in chain
        assert "ip daddr 172.16.0.0/12 drop" in chain

    def test_multiple_ingress_rules(self):
        cfg = {
            "ingress": {
                "policy": "drop",
                "rules": [
                    {"action": "accept", "source": "10.0.0.0/8"},
                    {"action": "accept", "source": "192.168.0.0/16", "port": 80, "protocol": "tcp"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert "ip saddr 10.0.0.0/8 accept" in chain
        assert "ip saddr 192.168.0.0/16 tcp dport 80 accept" in chain

    def test_extra_rules_placed_before_ingress_catchall(self):
        """Extra rules (e.g. 1:1 NAT) should appear between ingress rules and catch-all."""
        cfg = {
            "ingress": {
                "policy": "drop",
                "rules": [],
            },
        }
        extra = ['        iifname "upstream" oifname "dutif" ip daddr 192.168.100.10 accept']
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg, extra_rules=extra)
        assert "ip daddr 192.168.100.10 accept" in chain
        extra_pos = chain.index("ip daddr 192.168.100.10 accept")
        # Find the ingress catch-all (last line with "drop" that has no saddr)
        lines = chain.splitlines()
        ingress_catchall_line = [
            line for line in lines
            if '"upstream"' in line and '"dutif"' in line and line.strip().endswith("drop")
            and "saddr" not in line and "daddr" not in line
        ]
        assert len(ingress_catchall_line) == 1
        catchall_pos = chain.index(ingress_catchall_line[0].strip())
        assert extra_pos < catchall_pos

    def test_empty_filter_config(self):
        """Empty filter dict behaves like no filter."""
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config={})
        assert "ct state related,established accept" in chain
        assert 'iifname "dutif" oifname "upstream" accept' in chain
        assert 'iifname "upstream" oifname "dutif" accept' in chain

    def test_masquerade_with_filter_produces_valid_ruleset(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [{"action": "drop", "destination": "10.0.0.0/8"}],
            },
        }
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_masquerade_rules(
                "br-jmp0", "eth0", "192.168.100.0/24", filter_config=cfg,
            )
            ruleset = mock_load.call_args[0][0]
            assert "ct state related,established accept" in ruleset
            assert "ip daddr 10.0.0.0/8 drop" in ruleset
            assert "masquerade" in ruleset

    def test_1to1_with_filter_produces_valid_ruleset(self):
        cfg = {
            "ingress": {
                "policy": "drop",
                "rules": [
                    {"action": "accept", "source": "10.26.28.0/24", "port": 22, "protocol": "tcp"},
                ],
            },
        }
        mappings = [{"private_ip": "192.168.100.10", "public_ip": "10.0.0.50"}]
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_1to1_rules(
                "br-jmp0", "eth0", mappings, "192.168.100.0/24", filter_config=cfg,
            )
            ruleset = mock_load.call_args[0][0]
            assert "ct state related,established accept" in ruleset
            assert "ip saddr 10.26.28.0/24 tcp dport 22 accept" in ruleset
            assert "dnat to 192.168.100.10" in ruleset
            assert "snat to 10.0.0.50" in ruleset
            # 1:1 NAT extra forward rule should be present
            assert "ip daddr 192.168.100.10 accept" in ruleset

    def test_udp_protocol_in_rule(self):
        cfg = {
            "egress": {
                "policy": "accept",
                "rules": [
                    {"action": "drop", "destination": "0.0.0.0/0", "port": 53, "protocol": "udp"},
                ],
            },
        }
        chain = nftables._build_forward_chain("dutif", "upstream", filter_config=cfg)
        assert "udp dport 53 drop" in chain


class TestFlushRules:
    def test_flushes_specific_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules("my_custom_table")
            mock.assert_called_once_with(["delete", "table", "ip", "my_custom_table"], check=False)

    def test_flushes_default_table(self):
        with patch.object(nftables, "_run_nft") as mock:
            nftables.flush_rules()
            mock.assert_called_once_with(["delete", "table", "ip", "jumpstarter"], check=False)
