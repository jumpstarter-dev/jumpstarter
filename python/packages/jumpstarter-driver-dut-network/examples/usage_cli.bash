# Show full network status
j dut-network status

# List DHCP leases
j dut-network leases

# Look up DUT IP by MAC
j dut-network get-ip 8a:12:4e:25:f4:8e

# Add an address entry with a MAC (creates a DHCP static lease)
j dut-network add-address 192.168.100.50 --mac 02:00:00:aa:bb:cc --hostname my-dut

# Add an address entry without MAC (1:1 NAT mapping only, no DHCP lease)
j dut-network add-address 192.168.100.51 --public-ip 10.26.28.90

# Remove an address entry by IP
j dut-network remove-address 192.168.100.50

# Show nftables NAT rules
j dut-network nat-rules

# List configured DNS entries
j dut-network dns-entries

# Add a custom DNS entry
j dut-network add-dns controller.lab.local 10.26.28.1

# Remove a DNS entry
j dut-network remove-dns controller.lab.local
