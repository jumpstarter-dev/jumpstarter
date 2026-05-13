import ipaddress
import logging
import re
import subprocess
import textwrap

from ._privilege import sudo_cmd

logger = logging.getLogger(__name__)

_IFACE_RE = re.compile(r"^[a-zA-Z0-9._-]{1,15}$")


def _validate_iface(name: str) -> None:
    if not _IFACE_RE.match(name):
        raise ValueError(f"Invalid interface name: {name!r}")


def _validate_subnet(subnet: str) -> None:
    ipaddress.ip_network(subnet, strict=False)


def _validate_ip(ip: str) -> None:
    ipaddress.ip_address(ip)


def _run_nft(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = sudo_cmd(["nft"] + args)
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _load_ruleset(ruleset: str) -> None:
    logger.debug("Loading nftables ruleset:\n%s", ruleset)
    result = subprocess.run(
        sudo_cmd(["nft", "-f", "-"]),
        input=ruleset,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to load nftables ruleset: {result.stderr}")


def _table_name_for(interface: str) -> str:
    return f"jumpstarter_{interface}".replace("-", "_")


def _build_forward_chain(
    interface: str,
    upstream: str,
    filter_config: dict | None = None,
    extra_forward_rules: list[str] | None = None,
) -> str:
    """Build nftables forward chain rules with optional traffic filtering.

    When *filter_config* is ``None`` or empty the generated chain is identical
    to the legacy (pre-filter) behaviour so that existing setups are not
    affected.

    *extra_forward_rules* are additional pre-formatted nftables rule lines
    (e.g. per-mapping DNAT accept rules for 1:1 NAT).  In legacy mode they
    are appended after the conntrack line; in filtered mode they are inserted
    just before the ingress catch-all.

    When a filter configuration is provided the chain layout is::

        ct state related,established accept   # conntrack first
        <egress rules>                        # DUT -> upstream
        <egress catch-all>                    # egress policy
        <extra_forward_rules>                 # e.g. 1:1 NAT per-mapping accepts
        <ingress rules>                       # upstream -> DUT (new conns)
        <ingress catch-all>                   # ingress policy

    The chain *policy* is always ``accept`` so that forwarded traffic
    unrelated to this interface pair is not disturbed.
    """
    lines: list[str] = [
        "    chain forward {",
        "        type filter hook forward priority filter; policy accept;",
    ]

    extras = extra_forward_rules or []

    if not filter_config:
        # Legacy behaviour — no filtering.
        lines.append(f'        iifname "{interface}" oifname "{upstream}" accept')
        lines.append(
            f'        iifname "{upstream}" oifname "{interface}" ct state related,established accept'
        )
        lines.extend(extras)
        lines.append("    }")
        return "\n".join(lines)

    # --- Filtered mode -------------------------------------------------
    # Conntrack at the top: let established/related traffic through
    # regardless of direction.
    lines.append("        ct state related,established accept")

    egress = filter_config.get("egress", {})
    ingress = filter_config.get("ingress", {})

    # -- Egress (DUT -> upstream) ----------------------------------------
    for rule in egress.get("rules", []):
        parts = [f'        iifname "{interface}" oifname "{upstream}"']
        if "destination" in rule:
            parts.append(f"ip daddr {rule['destination']}")
        if "protocol" in rule:
            parts.append(rule["protocol"])
        if "port" in rule:
            parts.append(f"dport {rule['port']}")
        parts.append(rule["action"])
        lines.append(" ".join(parts))

    egress_policy = egress.get("policy", "accept")
    lines.append(f'        iifname "{interface}" oifname "{upstream}" {egress_policy}')

    # -- Extra forward rules (e.g. 1:1 NAT per-mapping accepts) ---------
    lines.extend(extras)

    # -- Ingress (upstream -> DUT) — new connections only ----------------
    for rule in ingress.get("rules", []):
        parts = [f'        iifname "{upstream}" oifname "{interface}"']
        if "source" in rule:
            parts.append(f"ip saddr {rule['source']}")
        if "protocol" in rule:
            parts.append(rule["protocol"])
        if "port" in rule:
            parts.append(f"dport {rule['port']}")
        parts.append(rule["action"])
        lines.append(" ".join(parts))

    ingress_policy = ingress.get("policy", "accept")
    lines.append(f'        iifname "{upstream}" oifname "{interface}" {ingress_policy}')

    lines.append("    }")
    return "\n".join(lines)


def apply_masquerade_rules(
    interface: str,
    upstream: str,
    subnet: str,
    table_name: str | None = None,
    filter_config: dict | None = None,
) -> None:
    _validate_iface(interface)
    _validate_iface(upstream)
    _validate_subnet(subnet)
    table = table_name or _table_name_for(interface)
    logger.info(
        "Applying masquerade rules: interface=%s upstream=%s subnet=%s table=%s",
        interface,
        upstream,
        subnet,
        table,
    )
    forward_chain = _build_forward_chain(interface, upstream, filter_config)
    ruleset = (
        f"table ip {table} {{\n"
        f"    chain postrouting {{\n"
        f"        type nat hook postrouting priority srcnat; policy accept;\n"
        f'        oifname "{upstream}" ip saddr {subnet} masquerade\n'
        f"    }}\n"
        f"{forward_chain}\n"
        f"}}\n"
    )
    flush_rules(table)
    _load_ruleset(ruleset)


def apply_1to1_rules(
    interface: str,
    upstream: str,
    mappings: list[dict[str, str]],
    subnet: str,
    table_name: str | None = None,
    filter_config: dict | None = None,
) -> None:
    _validate_iface(interface)
    _validate_iface(upstream)
    _validate_subnet(subnet)
    for m in mappings:
        _validate_ip(m["private_ip"])
        _validate_ip(m["public_ip"])
    table = table_name or _table_name_for(interface)
    logger.info(
        "Applying 1:1 NAT rules: interface=%s upstream=%s mappings=%d subnet=%s table=%s",
        interface,
        upstream,
        len(mappings),
        subnet,
        table,
    )

    prerouting_rules = []
    postrouting_rules = []
    extra_forward_rules = []
    output_rules = []

    for m in mappings:
        private_ip = m["private_ip"]
        public_ip = m["public_ip"]
        prerouting_rules.append(f'        iifname "{upstream}" ip daddr {public_ip} dnat to {private_ip}')
        postrouting_rules.append(f'        ip saddr {private_ip} oifname "{upstream}" snat to {public_ip}')
        extra_forward_rules.append(f'        iifname "{upstream}" oifname "{interface}" ip daddr {private_ip} accept')
        output_rules.append(f"        ip daddr {public_ip} dnat to {private_ip}")

    prerouting_block = "\n".join(prerouting_rules)
    postrouting_block = "\n".join(postrouting_rules)
    output_block = "\n".join(output_rules)

    forward_chain = _build_forward_chain(interface, upstream, filter_config, extra_forward_rules)

    ruleset = (
        f"table ip {table} {{\n"
        f"    chain prerouting {{\n"
        f"        type nat hook prerouting priority dstnat; policy accept;\n"
        f"{prerouting_block}\n"
        f"    }}\n"
        f"    chain output {{\n"
        f"        type nat hook output priority dstnat; policy accept;\n"
        f"{output_block}\n"
        f"    }}\n"
        f"    chain postrouting {{\n"
        f"        type nat hook postrouting priority srcnat; policy accept;\n"
        f"{postrouting_block}\n"
        f'        oifname "{upstream}" ip saddr {subnet} masquerade\n'
        f"    }}\n"
        f"{forward_chain}\n"
        f"}}\n"
    )

    flush_rules(table)
    _load_ruleset(ruleset)


def is_filter_forward_drop() -> bool:
    """Check if the nftables ``ip filter`` table has a FORWARD chain with policy drop.

    Docker (via iptables-nft) creates this to isolate container networks.
    Returns False when the table or chain does not exist.
    """
    result = _run_nft(["list", "chain", "ip", "filter", "FORWARD"], check=False)
    if result.returncode != 0:
        return False
    return "policy drop" in result.stdout


def ensure_filter_forward(interface: str, upstream: str) -> list[int]:
    """Insert nft ACCEPT rules into ``ip filter FORWARD`` if its policy is drop.

    Returns a list of rule handles so they can be removed on cleanup.
    """
    if not is_filter_forward_drop():
        return []

    handles: list[int] = []
    for iface in (interface, upstream):
        for direction in ("iifname", "oifname"):
            result = _run_nft(
                ["-e", "-a", "insert", "rule", "ip", "filter", "FORWARD",
                 direction, iface, "accept"],
                check=False,
            )
            if result.returncode == 0:
                match = re.search(r"# handle (\d+)", result.stdout)
                if match:
                    handles.append(int(match.group(1)))

    if handles:
        logger.info(
            "Inserted %d nft rules into ip filter FORWARD for %s and %s "
            "(policy was drop, likely set by Docker)",
            len(handles), interface, upstream,
        )
    return handles


def remove_filter_forward(handles: list[int]) -> None:
    """Remove nft rules previously inserted by ensure_filter_forward."""
    for handle in handles:
        _run_nft(
            ["delete", "rule", "ip", "filter", "FORWARD", "handle", str(handle)],
            check=False,
        )
    if handles:
        logger.info("Removed %d nft rules from ip filter FORWARD", len(handles))


def apply_ntp_redirect(interface: str, gateway_ip: str, table_name: str) -> None:
    """Redirect all NTP traffic (UDP 123) on *interface* to *gateway_ip*.

    Adds a DNAT rule in a dedicated prerouting chain so that any NTP
    client request arriving on the DUT-facing interface is redirected
    to the local NTP server listening on the gateway address.
    """
    _validate_iface(interface)
    _validate_ip(gateway_ip)
    ntp_table = f"{table_name}_ntp"
    logger.info(
        "Applying NTP redirect: interface=%s gateway=%s table=%s",
        interface, gateway_ip, ntp_table,
    )
    ruleset = textwrap.dedent(f"""\
        table ip {ntp_table} {{
            chain prerouting {{
                type nat hook prerouting priority dstnat; policy accept;
                iifname "{interface}" udp dport 123 dnat to {gateway_ip}:123
            }}
        }}
    """)
    flush_rules(ntp_table)
    _load_ruleset(ruleset)


def remove_ntp_redirect(table_name: str) -> None:
    """Remove the NTP redirect rules created by :func:`apply_ntp_redirect`."""
    ntp_table = f"{table_name}_ntp"
    logger.info("Removing NTP redirect table %s", ntp_table)
    flush_rules(ntp_table)


def flush_rules(table_name: str = "jumpstarter") -> None:
    logger.info("Flushing nftables table %s", table_name)
    _run_nft(["delete", "table", "ip", table_name], check=False)


def list_rules(table_name: str = "jumpstarter") -> str:
    result = _run_nft(["list", "table", "ip", table_name], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout
