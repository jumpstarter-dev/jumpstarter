import ipaddress
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from . import dnsmasq, iproute, nftables
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

        if missing:
            raise RuntimeError(
                f"DutNetwork driver requires the following tools: {', '.join(missing)}. "
                "Install them with: apt-get install -y iproute2 nftables dnsmasq-base"
            )

    def _nat_disabled(self) -> bool:
        return self.nat_mode in ("disabled", "none")

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

        self.logger.info(
            "DUT network configured: interface=%s subnet=%s nat=%s",
            self.interface,
            self.subnet,
            self.nat_mode,
        )

    def _get_1to1_mappings(self) -> list[dict[str, str]]:
        return [
            {"private_ip": entry["ip"], "public_ip": entry["public_ip"]}
            for entry in self.addresses
            if entry.get("public_ip")
        ]

    def cleanup(self) -> None:
        self.logger.info("Cleaning up DUT network configuration")

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
            new_entry["public_ip"] = public_ip

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
