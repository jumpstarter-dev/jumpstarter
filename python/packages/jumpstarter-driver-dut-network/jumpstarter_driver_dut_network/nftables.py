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


def _table_name_for(bridge: str) -> str:
    return f"jumpstarter_{bridge}".replace("-", "_")


def apply_masquerade_rules(
    bridge: str,
    upstream: str,
    subnet: str,
    table_name: str | None = None,
) -> None:
    _validate_iface(bridge)
    _validate_iface(upstream)
    table = table_name or _table_name_for(bridge)
    logger.info(
        "Applying masquerade rules: bridge=%s upstream=%s subnet=%s table=%s",
        bridge,
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
                iifname "{bridge}" oifname "{upstream}" accept
                iifname "{upstream}" oifname "{bridge}" ct state related,established accept
            }}
        }}
    """)
    flush_rules(table)
    _load_ruleset(ruleset)


def apply_1to1_rules(
    bridge: str,
    upstream: str,
    mappings: list[dict[str, str]],
    subnet: str,
    table_name: str | None = None,
) -> None:
    _validate_iface(bridge)
    _validate_iface(upstream)
    table = table_name or _table_name_for(bridge)
    logger.info(
        "Applying 1:1 NAT rules: bridge=%s upstream=%s mappings=%d subnet=%s table=%s",
        bridge,
        upstream,
        len(mappings),
        subnet,
        table,
    )

    prerouting_rules = []
    postrouting_rules = []
    forward_rules = []

    for m in mappings:
        private_ip = m["private_ip"]
        public_ip = m["public_ip"]
        prerouting_rules.append(f'        iifname "{upstream}" ip daddr {public_ip} dnat to {private_ip}')
        postrouting_rules.append(f'        ip saddr {private_ip} oifname "{upstream}" snat to {public_ip}')
        forward_rules.append(f'        iifname "{upstream}" oifname "{bridge}" ip daddr {private_ip} accept')

    prerouting_block = "\n".join(prerouting_rules)
    postrouting_block = "\n".join(postrouting_rules)
    forward_block = "\n".join(forward_rules)

    ruleset = (
        f"table ip {table} {{\n"
        f"    chain prerouting {{\n"
        f"        type nat hook prerouting priority dstnat; policy accept;\n"
        f"{prerouting_block}\n"
        f"    }}\n"
        f"    chain postrouting {{\n"
        f"        type nat hook postrouting priority srcnat; policy accept;\n"
        f"{postrouting_block}\n"
        f'        oifname "{upstream}" ip saddr {subnet} masquerade\n'
        f"    }}\n"
        f"    chain forward {{\n"
        f"        type filter hook forward priority filter; policy accept;\n"
        f'        iifname "{bridge}" oifname "{upstream}" accept\n'
        f'        iifname "{upstream}" oifname "{bridge}" ct state related,established accept\n'
        f"{forward_block}\n"
        f"    }}\n"
        f"}}\n"
    )

    flush_rules(table)
    _load_ruleset(ruleset)


def flush_rules(table_name: str = "jumpstarter") -> None:
    logger.info("Flushing nftables table %s", table_name)
    _run_nft(["delete", "table", "ip", table_name], check=False)


def list_rules(table_name: str = "jumpstarter") -> str:
    result = _run_nft(["list", "table", "ip", table_name], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout
