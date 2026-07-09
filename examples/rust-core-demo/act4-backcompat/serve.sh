#!/usr/bin/env bash
# Act 4, terminal A — host a NEW Rust-core exporter with a plain Python MockPower driver. The
# old client's legacy DriverCall protocol is served by the exporter's translation shim
# (rust/jumpstarter-driver-core/src/legacy.rs).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp run --exporter demo-compat
