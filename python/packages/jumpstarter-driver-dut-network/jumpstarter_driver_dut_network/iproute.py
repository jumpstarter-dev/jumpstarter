"""Interface management via iproute2 commands with NetworkManager awareness."""

import logging
import shutil
import subprocess

from ._privilege import sudo_cmd

logger = logging.getLogger(__name__)


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a read-only command (no sudo)."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _run_priv(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a privileged command, using sudo when not root."""
    cmd = sudo_cmd(cmd)
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def is_nm_running() -> bool:
    """Check if NetworkManager is active on this system."""
    if not shutil.which("nmcli"):
        return False
    result = _run(["nmcli", "general", "status"], check=False)
    return result.returncode == 0


def nm_set_unmanaged(interface: str) -> None:
    """Tell NetworkManager to stop managing an interface."""
    if is_nm_running():
        logger.info("Setting interface %s as unmanaged by NetworkManager", interface)
        _run_priv(["nmcli", "device", "set", interface, "managed", "no"], check=False)


def nm_set_managed(interface: str) -> None:
    """Restore NetworkManager management of an interface."""
    if is_nm_running():
        logger.info("Restoring NetworkManager management of interface %s", interface)
        _run_priv(["nmcli", "device", "set", interface, "managed", "yes"], check=False)


def configure_interface(interface: str, gateway_ip: str, prefix_len: int) -> None:
    """Flush existing addresses, assign the gateway IP, and bring the interface up."""
    logger.info("Configuring %s with IP %s/%d", interface, gateway_ip, prefix_len)
    _run_priv(["ip", "addr", "flush", "dev", interface])
    _run_priv(["ip", "addr", "add", f"{gateway_ip}/{prefix_len}", "dev", interface])
    _run_priv(["ip", "link", "set", interface, "up"])


def deconfigure_interface(interface: str) -> None:
    """Flush addresses and bring the interface down."""
    logger.info("Deconfiguring interface %s", interface)
    _run_priv(["ip", "addr", "flush", "dev", interface], check=False)
    _run_priv(["ip", "link", "set", interface, "down"], check=False)


def add_ip_alias(interface: str, ip: str, prefix_len: int) -> None:
    """Add a secondary IP address to an interface.

    This operation is idempotent: if the address is already present on the
    interface, the call is silently skipped.
    """
    addr = f"{ip}/{prefix_len}"
    if addr in get_interface_addresses(interface):
        logger.info("IP alias %s already present on %s, skipping", addr, interface)
        return
    logger.info("Adding IP alias %s to %s", addr, interface)
    _run_priv(["ip", "addr", "add", addr, "dev", interface])


def remove_ip_alias(interface: str, ip: str, prefix_len: int) -> None:
    """Remove a secondary IP address from an interface."""
    logger.info("Removing IP alias %s/%d from %s", ip, prefix_len, interface)
    _run_priv(["ip", "addr", "del", f"{ip}/{prefix_len}", "dev", interface], check=False)


def get_interface_forwarding(iface: str) -> str:
    """Return the current per-interface forwarding value ("0" or "1")."""
    result = _run(["sysctl", "-n", f"net.ipv4.conf.{iface}.forwarding"], check=False)
    return result.stdout.strip() or "0"


def set_interface_forwarding(iface: str, enabled: bool) -> None:
    """Enable or disable IPv4 forwarding on a specific interface.

    Uses the per-interface sysctl (net.ipv4.conf.<iface>/forwarding) rather
    than the global net.ipv4.ip_forward to avoid turning the host into a
    full router on all interfaces.
    """
    value = "1" if enabled else "0"
    logger.info("Setting net.ipv4.conf.%s.forwarding=%s", iface, value)
    _run_priv(["sysctl", "-w", f"net.ipv4.conf.{iface}.forwarding={value}"])


def detect_upstream_interface() -> str | None:
    """Detect the default upstream interface by parsing the default route."""
    result = _run(["ip", "route", "show", "default"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = result.stdout.strip().split()
    try:
        dev_idx = parts.index("dev")
        return parts[dev_idx + 1]
    except (ValueError, IndexError):
        return None


def interface_exists(name: str) -> bool:
    """Check whether a network interface exists."""
    result = _run(["ip", "link", "show", name], check=False)
    return result.returncode == 0


def get_interface_addresses(name: str) -> list[str]:
    """Get IP addresses assigned to an interface."""
    result = _run(["ip", "-o", "-4", "addr", "show", "dev", name], check=False)
    if result.returncode != 0:
        return []
    addrs = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                addrs.append(parts[i + 1])
                break
    return addrs


def get_interface_prefix_len(name: str) -> int | None:
    """Return the prefix length of the first IPv4 address on an interface."""
    addrs = get_interface_addresses(name)
    if not addrs:
        return None
    try:
        return int(addrs[0].split("/")[1])
    except (IndexError, ValueError):
        return None
