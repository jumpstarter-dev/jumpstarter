#!/usr/bin/env bash
# Act 4, terminal B — drive the Rust-core exporter with an UNMODIFIED pre-rewrite client
# (jumpstarter 0.7.4 from PyPI, installed by install-old-client.sh). Its venv bin goes FIRST on
# PATH so the old jmp finds its own `j` and driver-client packages — exactly a real pre-rewrite
# user's environment. Old jmp takes the command with no `--` separator.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"

OLD_VENV="${OLD_JMP_DIR:-/tmp/jmp-old}/.venv"
[ -x "$OLD_VENV/bin/jmp" ] || {
  err "old client not installed — run: bash $DEMO_DIR/act4-backcompat/install-old-client.sh"
  exit 1
}

export PATH="$OLD_VENV/bin:$PATH"
export JUMPSTARTER_GRPC_INSECURE=1 JMP_DRIVERS_ALLOW=UNSAFE
rc=0
jmp shell --client "$DEMO_CLIENT" --selector example.com/dut=compat j power on || rc=$?
echo
if [ "$rc" -eq 0 ]; then
  log "old 0.7.4 client drove the Rust-core exporter: exit 0 (the legacy DriverCall round-tripped)"
else
  err "old client run failed: exit $rc"
fi
exit "$rc"
