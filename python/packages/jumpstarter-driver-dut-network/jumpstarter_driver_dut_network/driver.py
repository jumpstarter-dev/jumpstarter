import asyncio
import asyncio.subprocess
import ipaddress
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from . import dnsmasq, iproute, nftables
from .ntp_server import NtpServer
from jumpstarter.driver import Driver, export


class InterfaceStatus(TypedDict):
    name: str
    exists: bool
    addresses: list[str]


class NetworkStatus(TypedDict):
    interface_status: InterfaceStatus
    interface: str
    upstream: str | None
    subnet: str
    nat_mode: str
    dhcp_enabled: bool
    leases: list[dict]
    dns_entries: list[dict[str, str]]
    nat_rules: str


@dataclass(kw_only=True)
class DutNetwork(Driver):
    interface: str
    subnet: str = "192.168.100.0/24"
    gateway_ip: str = "192.168.100.1"
    upstream_interface: str | None = None

    dhcp_enabled: bool = True
    dhcp_range_start: str = "192.168.100.100"
    dhcp_range_end: str = "192.168.100.200"
    addresses: list[dict[str, str]] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=lambda: ["8.8.8.8", "8.8.4.4"])

    dns_entries: list[dict[str, str]] = field(default_factory=list)

    local_ntp: bool = False

    enable_tcpdump: bool = False

    state_dir: str | None = None
    nat_mode: Literal["masquerade", "1to1", "disabled", "none"] = "masquerade"
    public_interface: str | None = None

    _dnsmasq_process: subprocess.Popen | None = field(init=False, default=None)
    _state_path: Path | None = field(init=False, default=None)
    _upstream: str | None = field(init=False, default=None)
    _prefix_len: int = field(init=False, default=0)
    _table_name: str = field(init=False, default="jumpstarter")
    _prev_fwd_iface: str = field(init=False, default="0")
    _prev_fwd_upstream: str = field(init=False, default="0")
    _upstream_prefix_len: int = field(init=False, default=24)
    _added_aliases: set[str] = field(init=False, default_factory=set)
    _fwd_rule_handles: list[int] = field(init=False, default_factory=list)
    _ntp_server: NtpServer | None = field(init=False, default=None)
    _tcpdump_process: asyncio.subprocess.Process | None = field(init=False, default=None)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_dut_network.client.DutNetworkClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._table_name = nftables._table_name_for(self.interface)
        self._check_system_requirements()
        self._validate_config()
        try:
            self._setup_network()
        except Exception:
            self.cleanup()
            raise

    def _check_system_requirements(self) -> None:
        if sys.platform != "linux":
            raise RuntimeError("DutNetwork driver requires Linux (network namespaces, nftables)")

        missing = []
        if not shutil.which("ip"):
            missing.append("ip (iproute2)")
        if not shutil.which("nft") and not self._nat_disabled():
            missing.append("nft (nftables)")
        if not shutil.which("dnsmasq") and self.dhcp_enabled:
            missing.append("dnsmasq")
        if not shutil.which("sysctl") and not self._nat_disabled():
            missing.append("sysctl")
        if not shutil.which("tcpdump") and self.enable_tcpdump:
            missing.append("tcpdump")

        if missing:
            raise RuntimeError(
                f"DutNetwork driver requires the following tools: {', '.join(missing)}. "
                "Install them with: apt-get install -y iproute2 nftables dnsmasq-base"
            )

    def _nat_disabled(self) -> bool:
        return self.nat_mode in ("disabled", "none")

    @staticmethod
    def _resolve_ip(value: str) -> str:
        """Resolve *value* to an IPv4 address string.

        If *value* is already a valid IP address it is returned unchanged.
        Otherwise it is treated as a DNS hostname and resolved via
        :func:`socket.getaddrinfo`.  Only the first IPv4 result is used.

        Raises :class:`ValueError` when the hostname cannot be resolved.
        """
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            pass

        try:
            results = socket.getaddrinfo(value, None, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror:
            results = []

        if not results:
            raise ValueError(
                f"Cannot resolve hostname '{value}' to an IPv4 address. "
                "Provide a valid IP address or a resolvable DNS name."
            )

        # results[0] is (family, type, proto, canonname, sockaddr)
        # sockaddr for AF_INET is (address, port)
        return results[0][4][0]

    def _validate_config(self) -> None:
        network = ipaddress.ip_network(self.subnet, strict=False)
        self._prefix_len = network.prefixlen

        gateway = ipaddress.ip_address(self.gateway_ip)
        if gateway not in network:
            raise ValueError(f"Gateway {self.gateway_ip} is not within subnet {self.subnet}")

        if self.nat_mode == "1to1":
            has_public = any(entry.get("public_ip") for entry in self.addresses)
            if not has_public:
                raise ValueError("At least one address entry must have public_ip for 1:1 NAT mode")

    def _setup_network(self) -> None:
        if not self._nat_disabled():
            self._upstream = self.upstream_interface or iproute.detect_upstream_interface()
            if not self._upstream:
                raise RuntimeError("Cannot detect upstream interface and none was configured")
            upstream_for_alias = self.public_interface or self._upstream
            detected = iproute.get_interface_prefix_len(upstream_for_alias)
            if detected is not None:
                self._upstream_prefix_len = detected
        else:
            self._upstream = self.upstream_interface

        self._state_path = Path(self.state_dir) if self.state_dir else dnsmasq.state_dir_for_interface(self.interface)
        dnsmasq.ensure_state_dir(self._state_path)

        iproute.nm_set_unmanaged(self.interface)
        iproute.configure_interface(self.interface, self.gateway_ip, self._prefix_len)

        if not self._nat_disabled():
            self._prev_fwd_iface = iproute.get_interface_forwarding(self.interface)
            self._prev_fwd_upstream = iproute.get_interface_forwarding(self._upstream)
            iproute.set_interface_forwarding(self.interface, True)
            iproute.set_interface_forwarding(self._upstream, True)
            self._fwd_rule_handles = nftables.ensure_filter_forward(self.interface, self._upstream)

        if self.dhcp_enabled:
            dnsmasq.write_config(
                state_dir=self._state_path,
                interface=self.interface,
                range_start=self.dhcp_range_start,
                range_end=self.dhcp_range_end,
                static_leases=[e for e in self.addresses if e.get("mac")],
                dns_servers=self.dns_servers,
                gateway_ip=self.gateway_ip,
                dns_entries=self.dns_entries,
            )
            self._dnsmasq_process = dnsmasq.start(self._state_path)

        upstream_for_nat = self._upstream
        if self.nat_mode == "masquerade":
            nftables.apply_masquerade_rules(
                self.interface, upstream_for_nat, self.subnet,
                table_name=self._table_name,
            )
        elif self.nat_mode == "1to1":
            mappings = self._get_1to1_mappings()
            upstream_for_alias = self.public_interface or self._upstream
            for m in mappings:
                ip = m["public_ip"]
                iproute.add_ip_alias(upstream_for_alias, ip, self._upstream_prefix_len)
                self._added_aliases.add(ip)
            nftables.apply_1to1_rules(
                self.interface, upstream_for_alias, mappings, self.subnet,
                table_name=self._table_name,
            )

        if self.local_ntp:
            self._ntp_server = NtpServer(self.gateway_ip)
            self._ntp_server.start()
            nftables.apply_ntp_redirect(self.interface, self.gateway_ip, self._table_name)

        self.logger.info(
            "DUT network configured: interface=%s subnet=%s nat=%s local_ntp=%s",
            self.interface,
            self.subnet,
            self.nat_mode,
            self.local_ntp,
        )

    def _get_1to1_mappings(self) -> list[dict[str, str]]:
        return [
            {"private_ip": entry["ip"], "public_ip": self._resolve_ip(entry["public_ip"])}
            for entry in self.addresses
            if entry.get("public_ip")
        ]

    def _stop_tcpdump(self) -> None:
        if self._tcpdump_process is not None:
            try:
                self._tcpdump_process.terminate()
            except ProcessLookupError:
                pass
            self._tcpdump_process = None

    def cleanup(self) -> None:
        self.logger.info("Cleaning up DUT network configuration")

        if self._ntp_server is not None:
            self._ntp_server.stop()
            self._ntp_server = None
            nftables.remove_ntp_redirect(self._table_name)

        self._stop_tcpdump()

        if self._dnsmasq_process:
            dnsmasq.stop(process=self._dnsmasq_process, state_dir=self._state_path)
            self._dnsmasq_process = None

        nftables.flush_rules(self._table_name)

        if self.nat_mode == "1to1":
            upstream_for_alias = self.public_interface or self._upstream
            if upstream_for_alias:
                for ip in list(self._added_aliases):
                    iproute.remove_ip_alias(upstream_for_alias, ip, self._upstream_prefix_len)
                self._added_aliases.clear()

        if self._fwd_rule_handles:
            nftables.remove_filter_forward(self._fwd_rule_handles)
            self._fwd_rule_handles = []

        if not self._nat_disabled():
            if self._prev_fwd_iface == "0":
                iproute.set_interface_forwarding(self.interface, False)
            if self._prev_fwd_upstream == "0" and self._upstream:
                iproute.set_interface_forwarding(self._upstream, False)

        iproute.deconfigure_interface(self.interface)
        iproute.nm_set_managed(self.interface)

    def close(self):
        self.cleanup()
        super().close()

    @export
    def status(self) -> NetworkStatus:
        iface_exists = iproute.interface_exists(self.interface)
        addresses = iproute.get_interface_addresses(self.interface) if iface_exists else []
        leases = self._get_leases_list()
        nat_rules = nftables.list_rules(self._table_name)

        return NetworkStatus(
            interface_status=InterfaceStatus(
                name=self.interface,
                exists=iface_exists,
                addresses=addresses,
            ),
            interface=self.interface,
            upstream=self._upstream,
            subnet=self.subnet,
            nat_mode=self.nat_mode,
            dhcp_enabled=self.dhcp_enabled,
            leases=leases,
            dns_entries=self.dns_entries,
            nat_rules=nat_rules,
        )

    @export
    def ntp_status(self) -> dict:
        """Return the status of the local NTP server."""
        return {
            "enabled": self.local_ntp,
            "running": self._ntp_server is not None and self._ntp_server.running,
        }

    @export
    def get_dut_ip(self, mac: str) -> str | None:
        if not self._state_path:
            return None
        lease = dnsmasq.get_lease_by_mac(self._state_path, mac)
        return lease.ip if lease else None

    @export
    def get_leases(self) -> list[dict]:
        return self._get_leases_list()

    def _get_leases_list(self) -> list[dict]:
        if not self._state_path:
            return []
        leases = dnsmasq.parse_leases(self._state_path)
        return [
            {"mac": lease.mac, "ip": lease.ip, "hostname": lease.hostname, "expiry": lease.expiry} for lease in leases
        ]

    @export
    def add_address(self, ip: str, mac: str | None = None, hostname: str = "", public_ip: str | None = None) -> None:
        new_entry: dict[str, str] = {"ip": ip}
        if mac:
            new_entry["mac"] = mac
        if hostname:
            new_entry["hostname"] = hostname
        if public_ip:
            new_entry["public_ip"] = self._resolve_ip(public_ip)

        self.addresses = [entry for entry in self.addresses if entry["ip"] != ip]
        self.addresses.append(new_entry)
        self._reload_dnsmasq_config()
        if self.nat_mode == "1to1":
            self._sync_1to1_nat()
        self.logger.info("Added address: ip=%s mac=%s hostname=%s", ip, mac, hostname)

    @export
    def remove_address(self, ip: str) -> None:
        self.addresses = [entry for entry in self.addresses if entry["ip"] != ip]
        self._reload_dnsmasq_config()
        if self.nat_mode == "1to1":
            self._sync_1to1_nat()
        self.logger.info("Removed address for ip=%s", ip)

    def _sync_1to1_nat(self) -> None:
        upstream_for_alias = self.public_interface or self._upstream
        if not upstream_for_alias:
            return
        mappings = self._get_1to1_mappings()
        wanted = {m["public_ip"] for m in mappings}
        stale = self._added_aliases - wanted
        new = wanted - self._added_aliases

        for ip in stale:
            iproute.remove_ip_alias(upstream_for_alias, ip, self._upstream_prefix_len)
        for ip in new:
            iproute.add_ip_alias(upstream_for_alias, ip, self._upstream_prefix_len)
        self._added_aliases = wanted

        nftables.flush_rules(self._table_name)
        nftables.apply_1to1_rules(
            self.interface, upstream_for_alias, mappings, self.subnet,
            table_name=self._table_name,
        )

    @export
    def get_nat_rules(self) -> str:
        return nftables.list_rules(self._table_name)

    @export
    def get_dns_entries(self) -> list[dict[str, str]]:
        return list(self.dns_entries)

    @export
    def add_dns_entry(self, hostname: str, ip: str) -> None:
        self.dns_entries = [e for e in self.dns_entries if e["hostname"] != hostname]
        self.dns_entries.append({"hostname": hostname, "ip": ip})
        self._reload_dnsmasq_config()
        self.logger.info("Added DNS entry: %s -> %s", hostname, ip)

    @export
    def remove_dns_entry(self, hostname: str) -> None:
        self.dns_entries = [e for e in self.dns_entries if e["hostname"] != hostname]
        self._reload_dnsmasq_config()
        self.logger.info("Removed DNS entry: %s", hostname)

    def _reload_dnsmasq_config(self) -> None:
        if self._state_path and self.dhcp_enabled:
            dnsmasq.write_config(
                state_dir=self._state_path,
                interface=self.interface,
                range_start=self.dhcp_range_start,
                range_end=self.dhcp_range_end,
                static_leases=[e for e in self.addresses if e.get("mac")],
                dns_servers=self.dns_servers,
                gateway_ip=self.gateway_ip,
                dns_entries=self.dns_entries,
            )
            dnsmasq.reload_config(process=self._dnsmasq_process, state_dir=self._state_path)

    @staticmethod
    def _sanitize_tcpdump_args(args: list[str]) -> list[str]:
        """Filter out disallowed tcpdump flags from user-supplied arguments.

        Prevents the caller from overriding the interface (``-i``) or writing
        to files (``-w``), which could conflict with the driver's own
        interface enforcement or expose the host filesystem.
        """
        blocked = {"-i", "--interface", "-w"}
        sanitized: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg in blocked:
                skip_next = True
                continue
            # Handle --flag=value form
            if any(arg.startswith(f"{b}=") for b in blocked):
                continue
            sanitized.append(arg)
        return sanitized

    @export
    async def tcpdump(self, args: list[str] | None = None) -> AsyncGenerator[str, None]:
        """Run tcpdump on the configured interface and stream output lines.

        The interface is always enforced to ``self.interface``; callers
        cannot override it. The ``-w`` flag is also blocked to prevent
        writing to the host filesystem. Additional tcpdump arguments can
        be passed via *args*.

        Requires ``enable_tcpdump: true`` in the driver config.

        Note on pcap streaming: tcpdump supports ``-w -`` to write pcap
        data to stdout, but this produces binary output that is not
        suitable for the text-based streaming transport used here. For
        pcap capture, consider running tcpdump directly on the exporter
        host and transferring the file via a separate mechanism.

        Args:
            args: Optional list of additional tcpdump arguments.

        Yields:
            Lines of tcpdump text output.
        """
        if not self.enable_tcpdump:
            raise RuntimeError(
                "tcpdump is not enabled. Set 'enable_tcpdump: true' in the driver config."
            )

        cmd = ["tcpdump", "-i", self.interface, "-l", "--packet-buffered"]
        if args:
            cmd.extend(self._sanitize_tcpdump_args(args))

        self.logger.info("Starting tcpdump: %s", " ".join(cmd))

        proc = await asyncio.subprocess.create_subprocess_exec(
            cmd[0],
            *cmd[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._tcpdump_process = proc

        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace").rstrip("\n")
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            self._tcpdump_process = None
