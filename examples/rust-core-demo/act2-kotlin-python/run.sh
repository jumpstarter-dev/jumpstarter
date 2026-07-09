#!/usr/bin/env bash
# Act 2, terminal B — lease the Python power driver and run the JUnit/Kotlin test next to this
# script (src/PowerNativeIT.kt: generated Kotlin PowerClient over the Rust UniFFI transport).
# The gradle module compiles src/ via an external test srcDir, so this file IS the test that runs.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"
demo_activate_venv

cd "$REPO_ROOT/java"
export JMP_DRIVERS_ALLOW=UNSAFE
exec jmp shell --client "$DEMO_CLIENT" --selector example.com/dut=mock -- \
  ./gradlew --console=plain :jumpstarter-driver-power-example:integrationTest \
  --tests "*PowerNativeIT" "$@"
