#!/usr/bin/env bash
# Act 1, terminal B — lease the QEMU DUT through the controller and run the ordinary Jumpstarter
# pytest next to this script (test_qemu_boot.py: power on -> serial login -> run a command).
# Extra args go to pytest; DEBUG_CONSOLE=1 mirrors the guest serial output live.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

cd "$REPO_ROOT"
export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp shell --client "$DEMO_CLIENT" --selector example.com/dut=qemu -- \
  pytest -s examples/rust-core-demo/act1-python-qemu/test_qemu_boot.py "$@"
