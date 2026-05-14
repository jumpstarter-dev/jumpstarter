import ipaddress
import logging
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ._privilege import signal_pid, sudo_cmd

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")

logger = logging.getLogger(__name__)

DEFAULT_STATE_BASE = "/var/lib/jumpstarter"


@dataclass
class DhcpLease:
    """Represents a DHCP lease entry from the dnsmasq lease file."""

    expiry: int
    mac: str
    ip: str
    hostname: str


def state_dir_for_interface(interface: str, base: str = DEFAULT_STATE_BASE) -> Path:
    """Return the state directory path for a given interface."""
    return Path(base) / f"dut-network-{interface}"


def ensure_state_dir(state_dir: Path) -> None:
    """Create the state directory with appropriate permissions.

    The directory is set to 0o755 so dnsmasq can still read files
    (like addn-hosts) after dropping privileges.
    """
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        subprocess.run(sudo_cmd(["mkdir", "-p", str(state_dir)]), check=True)
        uid = str(os.getuid())
        gid = str(os.getgid())
        subprocess.run(sudo_cmd(["chown", f"{uid}:{gid}", str(state_dir)]), check=True)
    try:
        state_dir.chmod(0o755)
    except PermissionError:
        subprocess.run(sudo_cmd(["chmod", "755", str(state_dir)]), check=True)
    logger.info("Ensured state directory: %s", state_dir)


def write_dns_hosts(state_dir: Path, dns_entries: list[dict[str, str]] | None = None) -> Path:
    """Write DNS entries to a hosts-format file that dnsmasq reloads on SIGHUP.

    Uses /etc/hosts format (IP HOSTNAME) so dnsmasq re-reads it on SIGHUP
    via the addn-hosts directive.  The file is made world-readable because
    dnsmasq drops privileges after startup (keep-in-foreground mode).
    """
    hosts_path = state_dir / "hosts.local"
    for e in dns_entries or []:
        ipaddress.ip_address(e["ip"])
        if not _HOSTNAME_RE.match(e["hostname"]):
            raise ValueError(f"Invalid hostname: {e['hostname']!r}")
    lines = [f"{e['ip']} {e['hostname']}" for e in (dns_entries or [])]
    hosts_path.write_text("\n".join(lines) + "\n" if lines else "")
    hosts_path.chmod(0o644)
    return hosts_path


def write_dhcp_hosts(state_dir: Path, static_leases: list[dict[str, str]]) -> Path:
    """Write static leases to a dhcp-hostsfile that dnsmasq re-reads on SIGHUP.

    Entries without a ``mac`` key are silently skipped because they represent
    address reservations used only for 1:1 NAT mappings, not DHCP bindings.
    """
    hosts_path = state_dir / "dhcp-hosts"
    lines = []
    for lease in static_leases:
        mac = lease.get("mac")
        if not mac:
            continue
        ip = lease["ip"]
        if not _MAC_RE.match(mac):
            raise ValueError(f"Invalid MAC address: {mac!r}")
        ipaddress.ip_address(ip)
        hostname = lease.get("hostname", "")
        if hostname and not _HOSTNAME_RE.match(hostname):
            raise ValueError(f"Invalid hostname: {hostname!r}")
        if hostname:
            lines.append(f"{mac},{ip},{hostname}")
        else:
            lines.append(f"{mac},{ip}")
    hosts_path.write_text("\n".join(lines) + "\n" if lines else "")
    hosts_path.chmod(0o644)
    return hosts_path


def write_config(
    state_dir: Path,
    interface: str,
    range_start: str,
    range_end: str,
    static_leases: list[dict[str, str]],
    dns_servers: list[str],
    gateway_ip: str,
    dns_entries: list[dict[str, str]] | None = None,
) -> Path:
    conf_path = state_dir / "dnsmasq.conf"
    lease_file = state_dir / "dnsmasq.leases"
    pid_file = state_dir / "dnsmasq.pid"
    hosts_path = state_dir / "hosts.local"
    dhcp_hosts_path = state_dir / "dhcp-hosts"

    write_dns_hosts(state_dir, dns_entries)
    write_dhcp_hosts(state_dir, static_leases)

    lines = [
        f"interface={interface}",
        "bind-interfaces",
        f"dhcp-range={range_start},{range_end},12h",
        f"dhcp-option=option:router,{gateway_ip}",
        f"dhcp-leasefile={lease_file}",
        f"pid-file={pid_file}",
        f"addn-hosts={hosts_path}",
        f"dhcp-hostsfile={dhcp_hosts_path}",
        "dhcp-sequential-ip",
        "log-dhcp",
        "no-resolv",
        "no-hosts",
        "keep-in-foreground",
    ]

    for server in dns_servers:
        lines.append(f"server={server}")

    conf_path.write_text("\n".join(lines) + "\n")
    logger.info("Wrote dnsmasq config to %s", conf_path)
    return conf_path


def _drain_pipe(pipe, log_fn) -> None:
    """Read from a pipe until EOF, forwarding each line to a logger."""
    try:
        for line in pipe:
            log_fn(line.decode(errors="replace").rstrip())
    except Exception:
        pass


_STARTUP_TIMEOUT = 2.0
_STARTUP_POLL_INTERVAL = 0.1


def start(state_dir: Path) -> subprocess.Popen:
    """Start dnsmasq as a foreground subprocess, using sudo when not root."""
    conf_path = state_dir / "dnsmasq.conf"
    if not conf_path.exists():
        raise FileNotFoundError(f"dnsmasq config not found: {conf_path}")

    pid_file = state_dir / "dnsmasq.pid"

    cmd = sudo_cmd(["dnsmasq", f"--conf-file={conf_path}"])
    logger.info("Starting dnsmasq: %s", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().decode() if process.stderr else ""
            raise RuntimeError(f"dnsmasq failed to start: {stderr}")
        if pid_file.exists():
            break
        time.sleep(_STARTUP_POLL_INTERVAL)

    if process.poll() is not None:
        stderr = process.stderr.read().decode() if process.stderr else ""
        raise RuntimeError(f"dnsmasq failed to start: {stderr}")

    if not pid_file.exists():
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("dnsmasq started but did not create pidfile within timeout")

    threading.Thread(
        target=_drain_pipe,
        args=(process.stderr, logger.debug),
        daemon=True,
    ).start()

    logger.info("dnsmasq started with PID %d", process.pid)
    return process


def _read_pid_file(state_dir: Path) -> int | None:
    """Read the dnsmasq PID from its pidfile."""
    pid_file = state_dir / "dnsmasq.pid"
    if pid_file.exists():
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def stop(process: subprocess.Popen | None = None, state_dir: Path | None = None) -> None:
    """Stop a running dnsmasq process, using sudo kill if not root.

    The pidfile is authoritative because dnsmasq may have been restarted
    externally, updating the pidfile but not our process handle.
    """
    pid = _read_pid_file(state_dir) if state_dir else None
    target_pid = pid or (process.pid if process else None)

    if target_pid is None:
        return

    logger.info("Stopping dnsmasq PID %d", target_pid)
    signal_pid(target_pid, signal.SIGTERM)

    if process and process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            signal_pid(target_pid, signal.SIGKILL)
            process.wait()


def reload_config(process: subprocess.Popen | None = None, state_dir: Path | None = None) -> None:
    """Send SIGHUP to dnsmasq to reload configuration (for lease changes).

    The pidfile is authoritative (same rationale as stop()).
    """
    pid = _read_pid_file(state_dir) if state_dir else None
    target_pid = pid or (process.pid if process and process.poll() is None else None)

    if target_pid is None:
        return

    logger.info("Sending SIGHUP to dnsmasq PID %d", target_pid)
    signal_pid(target_pid, signal.SIGHUP)


def parse_leases(state_dir: Path) -> list[DhcpLease]:
    """Parse the dnsmasq lease file and return current leases.

    Lease file format: <expiry_epoch> <mac> <ip> <hostname> <client_id>
    """
    lease_file = state_dir / "dnsmasq.leases"
    if not lease_file.exists():
        return []

    leases = []
    for line in lease_file.read_text().strip().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            leases.append(
                DhcpLease(
                    expiry=int(parts[0]),
                    mac=parts[1],
                    ip=parts[2],
                    hostname=parts[3] if parts[3] != "*" else "",
                )
            )
    return leases


def get_lease_by_mac(state_dir: Path, mac: str) -> DhcpLease | None:
    """Find a lease by MAC address."""
    mac_lower = mac.lower()
    for lease in parse_leases(state_dir):
        if lease.mac.lower() == mac_lower:
            return lease
    return None


def cleanup_state_dir(state_dir: Path) -> None:
    """Remove the state directory and its contents."""
    if state_dir.exists():
        import shutil

        shutil.rmtree(state_dir, ignore_errors=True)
        logger.info("Cleaned up state directory: %s", state_dir)


def update_config(
    state_dir: Path,
    interface: str,
    range_start: str,
    range_end: str,
    static_leases: list[dict[str, str]],
    dns_servers: list[str],
    gateway_ip: str,
    dns_entries: list[dict[str, str]] | None = None,
    process: subprocess.Popen | None = None,
) -> None:
    """Rewrite the dnsmasq config and reload the process."""
    write_config(state_dir, interface, range_start, range_end, static_leases, dns_servers, gateway_ip, dns_entries)
    reload_config(process=process, state_dir=state_dir)
