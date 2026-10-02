#!/bin/bash
# The demo workload, deployed onto the board over the `j mount` sshfs mount
# and started by jumpstarter-demo.service.
#
# It records the boot it ran under, which is what lets the test suite prove
# the workload came back by itself after the board was physically power
# cycled, rather than just still being up from before.
set -euo pipefail

state=/var/lib/jumpstarter-demo

mkdir -p "${state}"
cat > "${state}/last-run" <<EOF
boot_id=$(cat /proc/sys/kernel/random/boot_id)
started=$(date -Is)
kernel=$(uname -r)
EOF

echo "jumpstarter demo workload ran"
