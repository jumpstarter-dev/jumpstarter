#!/bin/bash
# The demo workload: a stand-in for an application, deployed onto the board
# over the `j mount` sshfs mount and then run over ssh by the test suite.
#
# It stamps each run with the boot it happened under. That is what lets
# test_survives_a_power_cycle tell a real post-reboot run from a stale
# last-run file left behind by the run before the power was cut: the test
# compares the boot_id recorded here against the board's current one.
set -euo pipefail

state=/var/lib/jumpstarter-demo

mkdir -p "${state}"
cat > "${state}/last-run" <<EOF
boot_id=$(cat /proc/sys/kernel/random/boot_id)
started=$(date -Is)
kernel=$(uname -r)
EOF

echo "jumpstarter demo workload ran"
