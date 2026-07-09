#!/usr/bin/env bash
# Act 3, terminal A — host the polyglot exporter: one process per driver, in three languages
# (Python MockPower, Rust rust:power via jmp-rust-host, Kotlin KotlinPowerDriver via the JVM
# exporter host), federated by the Rust hub under a single lease.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

# Per-language driver hosts (built by build-hosts.sh).
export JMP_DRIVER_HOST_PYTHON="$REPO_ROOT/python/.venv/bin/python"
export JMP_RUST_DRIVER_HOST="$REPO_ROOT/rust/target/debug/jmp-rust-host"
JMP_JVM_DRIVER_HOST="$(ls "$REPO_ROOT"/java/jumpstarter-driver-power-example/build/install/*/bin/jumpstarter-exporter-host 2>/dev/null | head -1 || true)"
export JMP_JVM_DRIVER_HOST

for host in "$JMP_RUST_DRIVER_HOST" "$JMP_JVM_DRIVER_HOST"; do
  [ -n "$host" ] && [ -x "$host" ] || {
    err "driver hosts not built — run: bash $DEMO_DIR/act3-polyglot/build-hosts.sh"
    exit 1
  }
done

export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp run --exporter demo-polyglot
