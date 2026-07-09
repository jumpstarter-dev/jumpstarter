#!/usr/bin/env bash
# Tear down the rust-core-demo identities. By default this removes the demo client + exporter
# CRs and their local config files but LEAVES the kind cluster running (so you can redeploy fast).
# Pass --cluster to also delete the kind cluster.
#
#   bash cluster/down.sh            # remove demo client/exporters only
#   bash cluster/down.sh --cluster  # also `kind delete cluster --name jumpstarter`
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
demo_activate_venv

for name in demo-qemu demo-mock demo-polyglot demo-compat; do
  jmp admin delete exporter "$name" -n "$NS" --context "$KCTX" >/dev/null 2>&1 && log "deleted exporter $name" || warn "exporter $name not present"
  rm -f "$EXPORTER_DIR/$name.yaml"
done

jmp admin delete client "$DEMO_CLIENT" -n "$NS" --context "$KCTX" --delete >/dev/null 2>&1 && log "deleted client $DEMO_CLIENT" || warn "client $DEMO_CLIENT not present"

if [ "${1:-}" = "--cluster" ]; then
  warn "deleting the kind cluster 'jumpstarter'"
  kind delete cluster --name jumpstarter
fi
log "teardown complete"
