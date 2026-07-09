#!/usr/bin/env bash
# Build the per-language driver hosts Act 3 needs and print the env exports that pin the polyglot
# hub to them. Source-friendly: run it, then copy the printed exports into the terminal where you
# run `jmp run --exporter demo-polyglot`.
#
#   bash examples/rust-core-demo/act3-polyglot/build-hosts.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
cd "$REPO_ROOT"

log "building the native Rust driver host (jmp-rust-host) — serves rust:power"
# The Rust workspace lives under rust/ (no Cargo.toml at the repo root).
( cd "$REPO_ROOT/rust" && cargo build -p jumpstarter-driver-example --bin jmp-rust-host )
RUST_HOST="$REPO_ROOT/rust/target/debug/jmp-rust-host"
[ -x "$RUST_HOST" ] || { err "jmp-rust-host not found at $RUST_HOST"; exit 1; }

log "pre-building the Act-3 Rust test (power_test.rs) so run.sh doesn't compile inside the lease"
( cd "$REPO_ROOT/rust" && cargo test -p jumpstarter-driver-power-pure-client --test rust_core_demo --no-run )

log "building the JVM driver host (installDist) — serves jvm:KotlinPowerDriver"
( cd "$REPO_ROOT/java" && ./gradlew --console=plain :jumpstarter-driver-power-example:installDist )
JVM_HOST="$(ls "$REPO_ROOT"/java/jumpstarter-driver-power-example/build/install/*/bin/jumpstarter-exporter-host 2>/dev/null | head -1 || true)"
[ -n "$JVM_HOST" ] && [ -x "$JVM_HOST" ] || { err "jumpstarter-exporter-host start script not found under build/install"; exit 1; }

PY_HOST="$REPO_ROOT/python/.venv/bin/python"

cat <<EOF

Hosts built. Export these in the terminal that runs the exporter, then start it:

  export JMP_DRIVER_HOST_PYTHON="$PY_HOST"
  export JMP_RUST_DRIVER_HOST="$RUST_HOST"
  export JMP_JVM_DRIVER_HOST="$JVM_HOST"
  export JMP_DRIVERS_ALLOW=UNSAFE
  jmp run --exporter demo-polyglot
EOF
