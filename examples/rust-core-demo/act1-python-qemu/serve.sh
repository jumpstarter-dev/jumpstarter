#!/usr/bin/env bash
# Act 1, terminal A — host the QEMU exporter (registers with the controller, then spawns
# qemu-system-aarch64 on this machine when the test calls power.on()).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

[ -f "$DEMO_DIR/act1-python-qemu/assets/disk.qcow2" ] || {
  err "no boot image yet — run: bash $DEMO_DIR/act1-python-qemu/fetch-image.sh"
  exit 1
}

cd "$REPO_ROOT" # the exporter config's asset paths are repo-root-relative
export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp run --exporter demo-qemu
