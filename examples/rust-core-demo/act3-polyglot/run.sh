#!/usr/bin/env bash
# Act 3, terminal B — lease the polyglot exporter once and drive all three drivers — a Python,
# a Rust, and a Kotlin implementation of the same PowerInterface — first interactively via the
# native `j` client, then programmatically via the NATIVE RUST TEST next to this script
# (power_test.rs: the generated typed PowerClient, `cargo test`), all inside the same lease.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv
export REPO_ROOT

export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp shell --client "$DEMO_CLIENT" --selector example.com/dut=polyglot -- sh -c '
  echo "=== the exporter tree: three drivers, three languages, one lease ==="
  j
  echo && echo "=== python driver ===" && j pypower on   && echo "pypower   on: OK"
  echo && echo "=== rust driver ==="   && j rustpower on && echo "rustpower on: OK"
  echo && echo "=== kotlin driver ===" && j jvmpower on  && echo "jvmpower  on: OK"
  echo && echo "=== the same three drivers from a native Rust test (power_test.rs) ==="
  cargo test --manifest-path "$REPO_ROOT/rust/Cargo.toml" \
    -p jumpstarter-driver-power-pure-client --test rust_core_demo -- --nocapture
'
