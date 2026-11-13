#!/bin/sh
# Create the virtual Ethernet link that Device Hub uses to reach leased iOS
# devices, and let the invoking user read and write raw frames on it.
#
# macOS checks that a network iOS device answers ARP/NDP on its link. A leased
# device is reached through Jumpstarter, so the client answers for it on feth1
# while macOS talks to it through feth0. Run once per boot:
#
#   sudo sh devicehub-link-macos.sh
#
# Granting BPF access lets the user capture packets on every interface until
# the next reboot, as Wireshark's ChmodBPF does. macOS adds a root-owned capture
# device whenever one is opened, so the script is safe to run again to hand
# those over.
set -eu

user=${SUDO_USER:?run this script with sudo}
host_address=fd6a:7374:6a73::1

ifconfig feth0 >/dev/null 2>&1 || ifconfig feth0 create
ifconfig feth1 >/dev/null 2>&1 || ifconfig feth1 create
ifconfig feth0 | grep -q 'peer: feth1' || ifconfig feth0 peer feth1
ifconfig feth0 | grep -q "inet6 $host_address " || ifconfig feth0 inet6 "$host_address" prefixlen 64 alias
ifconfig feth0 up
ifconfig feth1 up

for device in /dev/bpf*; do
    chown "$user" "$device"
    chmod 600 "$device"
done

echo "feth0 ($host_address) is linked to feth1; $user can use BPF until reboot."
