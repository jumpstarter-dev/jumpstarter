"""Bridge and interface management via iproute2 commands with NetworkManager awareness."""

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


def create_bridge(name: str, gateway_ip: str, prefix_len: int) -> None:
    """Create a bridge interface and assign an IP address."""
    logger.info("Creating bridge %s with IP %s/%d", name, gateway_ip, prefix_len)
    _run_priv(["ip", "link", "add", name, "type", "bridge"])
    _run_priv(["ip", "addr", "add", f"{gateway_ip}/{prefix_len}", "dev", name])
    _run_priv(["ip", "link", "set", name, "up"])


def delete_bridge(name: str) -> None:
    """Remove a bridge interface."""
    logger.info("Deleting bridge %s", name)
    _run_priv(["ip", "link", "set", name, "down"], check=False)
    _run_priv(["ip", "link", "del", name], check=False)


def add_slave(bridge_name: str, interface: str) -> None:
    """Add an interface as a slave to a bridge."""
    logger.info("Adding %s as slave to bridge %s", interface, bridge_name)
    _run_priv(["ip", "link", "set", interface, "master", bridge_name])
    _run_priv(["ip", "link", "set", interface, "up"])


def remove_slave(interface: str) -> None:
    """Remove an interface from its bridge."""
    logger.info("Removing %s from bridge", interface)
    _run_priv(["ip", "link", "set", interface, "nomaster"], check=False)


def add_ip_alias(interface: str, ip: str, prefix_len: int) -> None:
    """Add a secondary IP address to an interface."""
    logger.info("Adding IP alias %s/%d to %s", ip, prefix_len, interface)
    _run_priv(["ip", "addr", "add", f"{ip}/{prefix_len}", "dev", interface])


def remove_ip_alias(interface: str, ip: str, prefix_len: int) -> None:
    """Remove a secondary IP address from an interface."""
    logger.info("Removing IP alias %s/%d from %s", ip, prefix_len, interface)
    _run_priv(["ip", "addr", "del", f"{ip}/{prefix_len}", "dev", interface], check=False)


def get_ip_forwarding() -> str:
    """Return the current value of net.ipv4.ip_forward ("0" or "1")."""
    result = _run(["sysctl", "-n", "net.ipv4.ip_forward"], check=False)
    return result.stdout.strip() or "0"


def set_ip_forwarding(enabled: bool) -> None:
    """Enable or disable IPv4 forwarding via sysctl."""
    value = "1" if enabled else "0"
    logger.info("Setting net.ipv4.ip_forward=%s", value)
    _run_priv(["sysctl", "-w", f"net.ipv4.ip_forward={value}"])


def detect_upstream_interface() -> str | None:
    """Detect the default upstream interface by parsing the default route."""
    result = _run(["ip", "route", "show", "default"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Format: "default via <gw> dev <iface> ..."
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


def get_bridge_slaves(bridge_name: str) -> list[str]:
    """List interfaces enslaved to a bridge."""
    result = _run(["ip", "-o", "link", "show", "master", bridge_name], check=False)
    if result.returncode != 0:
        return []
    slaves = []
    for line in result.stdout.strip().splitlines():
        # Format: "4: eth1@if3: <...> master br0 ..."
        parts = line.split(":")
        if len(parts) >= 2:
            iface = parts[1].strip().split("@")[0]
            slaves.append(iface)
    return slaves


def get_interface_addresses(name: str) -> list[str]:
    """Get IP addresses assigned to an interface."""
    result = _run(["ip", "-o", "-4", "addr", "show", "dev", name], check=False)
    if result.returncode != 0:
        return []
    addrs = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        # Format: "idx: name inet IP/prefix scope ..."
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                addrs.append(parts[i + 1])
                break
    return addrs


def get_interface_prefix_len(name: str) -> int | None:
    """Return the prefix length of the first IPv4 address on an interface, or None."""
    result = _run(["ip", "-o", "-4", "addr", "show", "dev", name], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        # Format: "idx: name inet IP/prefix scope ..."
        for i, part in enumerate(parts):
            if part == "inet" and i + 1 < len(parts):
                try:
                    return int(parts[i + 1].split("/")[1])
                except (IndexError, ValueError):
                    pass
    return None
