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

    # Manage address entries at runtime
    # With MAC: creates a DHCP static lease + optional 1:1 NAT mapping
    client.dut_network.add_address("192.168.100.50", mac="02:00:00:aa:bb:cc", hostname="new-dut")
    # Without MAC: 1:1 NAT mapping only (no DHCP lease)
    client.dut_network.add_address("192.168.100.51", public_ip="10.26.28.90")
    client.dut_network.remove_address("192.168.100.50")

    # Manage DNS entries at runtime
    client.dut_network.add_dns_entry("myhost.lab.local", "10.0.0.99")
    entries = client.dut_network.get_dns_entries()
    client.dut_network.remove_dns_entry("myhost.lab.local")
