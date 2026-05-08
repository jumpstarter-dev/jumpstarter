# DutNetwork Driver

`jumpstarter-driver-dut-network` provides network isolation for DUTs (Devices Under Test) by configuring a dedicated network interface with NAT, DHCP, and nftables-based firewall rules on the exporter host.

This enables scenarios where multiple DUTs share the same static IP configuration (common in automotive/embedded labs) by isolating each DUT behind its own NAT interface on the exporter.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-dut-network
```

### System Dependencies

The following must be available on the exporter host:

- `ip` (iproute2) - for interface management
- `nft` (nftables) - for NAT and firewall rules
- `dnsmasq` - for DHCP serving

Optional:
- `nmcli` (NetworkManager) - only needed if NM is running; the driver marks its interfaces as unmanaged

## How It Works

The driver configures an isolated network for the DUT:

1. Takes over a dedicated Ethernet interface (e.g., USB NIC) and assigns a gateway IP directly to it
2. Runs dnsmasq to provide DHCP to DUTs connected to that interface
3. Configures nftables rules for NAT (masquerade or 1:1)
4. Enables IP forwarding so DUT traffic routes through the exporter

When NetworkManager is detected, the driver marks managed interfaces as `unmanaged` to prevent interference. On cleanup, existing addresses are flushed and the interface is restored to NetworkManager control.

## Configuration

### Masquerade NAT (recommended for most use cases)

DUTs share the exporter's upstream IP when accessing the network:

```yaml
export:
  dut-network:
    type: jumpstarter_driver_dut_network.driver.DutNetwork
    config:
      interface: "eth2"
      subnet: "192.168.100.0/24"
      gateway_ip: "192.168.100.1"
      nat_mode: "masquerade"
      dhcp_enabled: true
      dhcp_range_start: "192.168.100.100"
      dhcp_range_end: "192.168.100.200"
      static_leases:
        - mac: "8a:12:4e:25:f4:8e"
          ip: "192.168.100.10"
          hostname: "sa8775p"
      dns_servers: ["8.8.8.8", "8.8.4.4"]
```

### 1:1 NAT

Each DUT gets a dedicated public IP alias via a per-lease `public_ip` field, enabling inbound connections from the LAN. DUTs without a `public_ip` fall back to masquerade for outbound traffic.

```yaml
export:
  dut-network:
    type: jumpstarter_driver_dut_network.driver.DutNetwork
    config:
      interface: "eth2"
      subnet: "192.168.100.0/24"
      gateway_ip: "192.168.100.1"
      upstream_interface: "enp2s0"
      nat_mode: "1to1"
      static_leases:
        - mac: "8a:12:4e:25:f4:8e"
          ip: "192.168.100.10"
          hostname: "sa8775p-1"
          public_ip: "10.26.28.84"
        - mac: "8a:12:4e:25:f4:8f"
          ip: "192.168.100.11"
          hostname: "sa8775p-2"
          public_ip: "10.26.28.85"
```

### Disabled NAT (DHCP only)

DHCP works normally but no NAT rules or IP forwarding are configured. Useful for pure L2 isolation or when routing is handled externally:

```yaml
export:
  dut-network:
    type: jumpstarter_driver_dut_network.driver.DutNetwork
    config:
      interface: "enx00e04c683af1"
      nat_mode: "disabled"   # also accepts "none"
      dhcp_enabled: true
```

### Custom DNS Entries

Register custom DNS records that dnsmasq will respond to. Useful for pointing DUTs at local services without a full DNS infrastructure:

```yaml
export:
  dut-network:
    type: jumpstarter_driver_dut_network.driver.DutNetwork
    config:
      interface: "eth2"
      nat_mode: "masquerade"
      dns_entries:
        - hostname: "controller.lab.local"
          ip: "10.26.28.1"
        - hostname: "registry.lab.local"
          ip: "10.26.28.2"
```

## Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `interface` | str | *required* | Physical NIC for DUT connectivity (e.g., USB NIC name) |
| `subnet` | str | `192.168.100.0/24` | Private subnet for DUTs |
| `gateway_ip` | str | `192.168.100.1` | IP assigned to the interface (acts as gateway for DUTs) |
| `upstream_interface` | str | auto-detect | Interface for outbound NAT traffic |
| `dhcp_enabled` | bool | `true` | Whether to run DHCP on the interface |
| `dhcp_range_start` | str | `192.168.100.100` | DHCP dynamic range start |
| `dhcp_range_end` | str | `192.168.100.200` | DHCP dynamic range end |
| `static_leases` | list | `[]` | Static DHCP leases: `{mac, ip, hostname, public_ip?}` |
| `dns_servers` | list | `[8.8.8.8, 8.8.4.4]` | DNS servers for DHCP clients |
| `dns_entries` | list | `[]` | Custom DNS records: `{hostname, ip}` |
| `state_dir` | str | `/var/lib/jumpstarter/dut-network-{interface}/` | Directory for dnsmasq state files |
| `nat_mode` | str | `masquerade` | NAT mode: `masquerade`, `1to1`, `disabled`, or `none` |
| `public_interface` | str | None | Interface for IP alias (defaults to upstream) |

### Static Lease Fields

| Field | Required | Description |
|-------|----------|-------------|
| `mac` | yes | MAC address of the DUT |
| `ip` | yes | Private IP to assign |
| `hostname` | no | Hostname for DHCP |
| `public_ip` | no | Public IP for 1:1 NAT (per-DUT). At least one lease must have `public_ip` when `nat_mode=1to1` |

## Client CLI

Inside a `jmp shell` session:

```shell
# Show full network status
j dut-network status

# List DHCP leases
j dut-network leases

# Look up DUT IP by MAC
j dut-network get-ip 8a:12:4e:25:f4:8e

# Add a static DHCP lease
j dut-network add-lease 02:00:00:aa:bb:cc 192.168.100.50 --hostname my-dut

# Remove a static lease
j dut-network remove-lease 02:00:00:aa:bb:cc

# Show nftables NAT rules
j dut-network nat-rules

# List configured DNS entries
j dut-network dns-entries

# Add a custom DNS entry
j dut-network add-dns controller.lab.local 10.26.28.1

# Remove a DNS entry
j dut-network remove-dns controller.lab.local
```

## Python API

```python
from jumpstarter.common.utils import env

with env() as client:
    # Get network status
    status = client.dut_network.status()
    print(status["interface_status"]["name"])

    # Get all DHCP leases
    leases = client.dut_network.get_leases()
    for lease in leases:
        print(f"{lease['mac']} -> {lease['ip']}")

    # Look up DUT IP
    ip = client.dut_network.get_dut_ip("8a:12:4e:25:f4:8e")

    # Manage static leases at runtime
    client.dut_network.add_static_lease("02:00:00:aa:bb:cc", "192.168.100.50", "new-dut")
    client.dut_network.remove_static_lease("02:00:00:aa:bb:cc")

    # Manage DNS entries at runtime
    client.dut_network.add_dns_entry("myhost.lab.local", "10.0.0.99")
    entries = client.dut_network.get_dns_entries()
    client.dut_network.remove_dns_entry("myhost.lab.local")
```

## nftables Coexistence

The driver uses a dedicated nftables table (named after the interface, e.g. `table ip jumpstarter_enx00e04c683af1`) that does not conflict with firewalld or other nftables users. Firewalld manages its own `firewalld` table and does not touch other tables, even during reloads.

## Architecture

```text
                     Exporter Host
 ┌─────────┐        ┌──────────────────────────────────────┐          ┌─────────┐
 │   DUT   │        │                                      │          │   LAN   │
 │         │  eth   │  eth2               ┌──────────┐     │          │         │
 │  DHCP   │◄──────►│  192.168.100.1/24   │ dnsmasq  │     │          │         │
 │  client │        │  (gateway)          │ DHCP+DNS │     │          │         │
 │         │        │       │             └──────────┘     │          │         │
 │ 192.168.│        │       │  forwarding                  │  eth     │         │
 │ 100.10  │        │       ▼             ┌──────────┐     │          │         │
 │         │        │  ┌─────────┐        │ nftables │     │ enp2s0   │ 10.26.  │
 └─────────┘        │  │ ip_fwd  │───────►│ NAT      │────►│◄──────►  │ 28.0/24 │
                    │  └─────────┘        │          │     │(upstream)│         │
                    │                     │masq/1:1  │     │          └─────────┘
                    │                     └──────────┘     │
                    └──────────────────────────────────────┘

  ─── Masquerade: DUT traffic appears as exporter's upstream IP
  ─── 1:1 NAT:    DUT gets a dedicated public IP on the upstream interface
```

### Disabled NAT (DHCP-only isolation)

```text
                     Exporter Host
 ┌─────────┐        ┌──────────────────────────────┐
 │   DUT   │        │                              │
 │         │  eth   │  eth2          ┌──────────┐  │
 │  DHCP   │◄──────►│  192.168.100.1 │ dnsmasq  │  │
 │  client │        │  (gateway)     │ DHCP+DNS │  │
 │         │        │                └──────────┘  │
 │ 192.168.│        │                              │
 │ 100.10  │        │  No forwarding, no NAT.      │
 │         │        │  L2-isolated network only.   │
 └─────────┘        └──────────────────────────────┘

  The DUT can reach the exporter on 192.168.100.1 but has
  no route to the LAN or internet. Useful for pure L2
  isolation or when routing is handled externally.
```

## Troubleshooting

### NAT traffic not forwarding (Docker hosts)

On hosts running Docker, the default iptables policy is often set to
`iptables -P FORWARD DROP` to isolate container networks.  Since modern
Linux translates iptables rules into nftables under the hood, this creates
a `table ip filter { chain FORWARD { policy drop } }` base chain that
**all** forwarded packets must pass — including traffic routed through
the DUT interface.

The driver **automatically** detects this situation using native nftables:
when NAT is enabled, it checks if the `ip filter` table's FORWARD chain
has `policy drop`.  If so, targeted `accept` rules are inserted directly
into that chain for the DUT and upstream interfaces on startup, and
removed by handle on cleanup.  No manual intervention or `iptables`
binary is required.

### Per-interface IP forwarding

The driver enables IPv4 forwarding only on the DUT and upstream
interfaces (`net.ipv4.conf.<iface>.forwarding=1`) rather than the global
`net.ipv4.ip_forward` sysctl.  This avoids turning a multi-homed host
into a full router on every interface.  If forwarding still does not work,
verify with:

```shell
sysctl net.ipv4.conf.<interface>.forwarding
sysctl net.ipv4.conf.<upstream>.forwarding
```

## Running Tests

Integration tests require root privileges through passwordless sudo, or direct root access:

```shell
make pkg-test-dut-network
```

Tests use veth pairs and network namespaces to simulate the DUT without real hardware.
