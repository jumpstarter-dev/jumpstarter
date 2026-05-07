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


def apply_masquerade_rules(
    interface: str,
    upstream: str,
    subnet: str,
    table_name: str | None = None,
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
    ruleset = textwrap.dedent(f"""\
        table ip {table} {{
            chain postrouting {{
                type nat hook postrouting priority srcnat; policy accept;
                oifname "{upstream}" ip saddr {subnet} masquerade
            }}
            chain forward {{
                type filter hook forward priority filter; policy accept;
                iifname "{interface}" oifname "{upstream}" accept
                iifname "{upstream}" oifname "{interface}" ct state related,established accept
            }}
        }}
    """)
    flush_rules(table)
    _load_ruleset(ruleset)


def apply_1to1_rules(
    interface: str,
    upstream: str,
    mappings: list[dict[str, str]],
    subnet: str,
    table_name: str | None = None,
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
    forward_rules = []
    output_rules = []

    for m in mappings:
        private_ip = m["private_ip"]
        public_ip = m["public_ip"]
        prerouting_rules.append(f'        iifname "{upstream}" ip daddr {public_ip} dnat to {private_ip}')
        postrouting_rules.append(f'        ip saddr {private_ip} oifname "{upstream}" snat to {public_ip}')
        forward_rules.append(f'        iifname "{upstream}" oifname "{interface}" ip daddr {private_ip} accept')
        output_rules.append(f"        ip daddr {public_ip} dnat to {private_ip}")

    prerouting_block = "\n".join(prerouting_rules)
    postrouting_block = "\n".join(postrouting_rules)
    forward_block = "\n".join(forward_rules)
    output_block = "\n".join(output_rules)

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
        f"    chain forward {{\n"
        f"        type filter hook forward priority filter; policy accept;\n"
        f'        iifname "{interface}" oifname "{upstream}" accept\n'
        f'        iifname "{upstream}" oifname "{interface}" ct state related,established accept\n'
        f"{forward_block}\n"
        f"    }}\n"
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


def flush_rules(table_name: str = "jumpstarter") -> None:
    logger.info("Flushing nftables table %s", table_name)
    _run_nft(["delete", "table", "ip", table_name], check=False)


def list_rules(table_name: str = "jumpstarter") -> str:
    result = _run_nft(["list", "table", "ip", table_name], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout
