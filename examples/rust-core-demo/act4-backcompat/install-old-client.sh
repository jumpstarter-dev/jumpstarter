#!/usr/bin/env bash
# Install an UNMODIFIED old Jumpstarter client (from PyPI) into an isolated venv, so Act 4 can
# drive the new Rust-core exporter with a pre-rewrite client and prove protocol backwards-compat.
#
#   bash examples/rust-core-demo/act4-backcompat/install-old-client.sh
#   -> prints OLD_JMP=<path to the old jmp binary>
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../cluster" && pwd)/lib.sh"

OLD_VER="${COMPAT_CLIENT_VERSION:-0.7.4}"
OLD_DIR="${OLD_JMP_DIR:-/tmp/jmp-old}"
PYV="$(cat "$REPO_ROOT/.py-version")"

log "installing old client v$OLD_VER into $OLD_DIR/.venv (python $PYV)"
rm -rf "$OLD_DIR"
uv venv "$OLD_DIR/.venv" --python "$PYV"
uv pip install --python "$OLD_DIR/.venv/bin/python" \
  "jumpstarter-cli==$OLD_VER" \
  "jumpstarter==$OLD_VER" \
  "jumpstarter-driver-power==$OLD_VER" \
  "jumpstarter-driver-composite==$OLD_VER"

"$OLD_DIR/.venv/bin/jmp" version >/dev/null 2>&1 && log "old jmp installed OK" || warn "old jmp 'version' failed (may still work)"
echo
echo "OLD_JMP=$OLD_DIR/.venv/bin/jmp"
echo "Use it in Act 4 (note: old jmp takes the command WITHOUT a '--' separator)."
