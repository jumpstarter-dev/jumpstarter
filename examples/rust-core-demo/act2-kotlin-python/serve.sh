#!/usr/bin/env bash
# Act 2, terminal A — host the Python MockPower exporter (the driver the Kotlin test drives).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp run --exporter demo-mock
