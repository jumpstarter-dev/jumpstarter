#!/usr/bin/env bash
# One-time bring-up for the rust-core-demo: build the Rust controller/router image, deploy it
# into a local kind cluster via the operator (using the CONTROLLER_IMG/ROUTER_IMG seam in
# controller/hack/deploy_vars), sync the Python packages (which also builds the native jmp/j
# CLIs + the jumpstarter_core extension), and create the demo client + per-act exporter configs.
#
# Auth is the internal/unsafe path (no dex/OIDC) so the old 0.7.4 client in Act 4 can also use it.
# TLS is the operator's self-signed cert; the demo client/exporters use -k (tls.insecure) so no
# CA install is needed.
#
# Env knobs:
#   CONTAINER_TOOL=podman|docker   (default: podman — matches the running podman machine)
#   SKIP_IMAGE=1                   skip `make rust-controller-image` (reuse a cached image)
#   SKIP_DEPLOY=1                  skip the image build + controller deploy entirely — use when the
#                                  kind cluster is already running the Rust controller (just (re)creates
#                                  the demo client + exporter configs)
#   SKIP_SYNC=1                    skip `make -C python sync`
#   IP=127.0.0.1                   pins the nip.io baseDomain to loopback so Docker/podman
#                                  published ports resolve on macOS (override if needed)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

export CONTAINER_TOOL="${CONTAINER_TOOL:-podman}"
if [ "$CONTAINER_TOOL" = "podman" ]; then export KIND_EXPERIMENTAL_PROVIDER="${KIND_EXPERIMENTAL_PROVIDER:-podman}"; fi

# Pin the controller baseDomain to loopback. On macOS, kind's published nodeports live on
# 127.0.0.1, so a nip.io domain built from the LAN IP would not route; 127.0.0.1.nip.io does.
export IP="${IP:-127.0.0.1}"

# The Rust controller rewrite seam (deploy_vars): point the CR's controller/router image at the
# image we build below instead of the default Go controller.
export CONTROLLER_IMG="$RUST_CONTROLLER_IMG"
export ROUTER_IMG="$RUST_CONTROLLER_IMG"

log "preflight"
require "$CONTAINER_TOOL"
require kind
require kubectl
require uv
require cargo

cd "$REPO_ROOT"

if [ -n "${SKIP_DEPLOY:-}" ]; then
  warn "SKIP_DEPLOY set — not building the image or redeploying; using the running cluster as-is"
  warn "current CR controller image: $(kubectl --context "$KCTX" -n "$NS" get jumpstarter jumpstarter -o jsonpath='{.spec.controller.image}' 2>/dev/null || echo '?')"
else
  if [ -z "${SKIP_IMAGE:-}" ]; then
    log "building the Rust controller/router image ($RUST_CONTROLLER_IMG) with $CONTAINER_TOOL — this compiles the Rust workspace, budget several minutes on a cold cache"
    CONTAINER_TOOL="$CONTAINER_TOOL" make rust-controller-image
  else
    warn "SKIP_IMAGE set — assuming $RUST_CONTROLLER_IMG already exists"
  fi

  log "deploying the operator + Jumpstarter CR (controller/router = Rust image) into kind"
  # `make -C controller deploy` creates the kind cluster if missing, builds+loads the operator
  # (and the unused Go controller image unless SKIP_BUILD=1), then runs deploy_with_operator.sh
  # which writes CONTROLLER_IMG/ROUTER_IMG into the Jumpstarter CR and loads them into the cluster.
  CONTAINER_TOOL="$CONTAINER_TOOL" make -C controller deploy
fi

if [ -z "${SKIP_SYNC:-}" ]; then
  log "syncing Python packages (builds the native jmp/j CLIs + jumpstarter_core extension)"
  make -C python sync
else
  warn "SKIP_SYNC set — assuming python/.venv is already built"
fi

demo_activate_venv
require jmp

log "ensuring exporter config dir $EXPORTER_DIR is writable"
if [ ! -d "$EXPORTER_DIR" ] || [ ! -w "$EXPORTER_DIR" ]; then
  warn "creating $EXPORTER_DIR (requires sudo)"
  sudo mkdir -p "$EXPORTER_DIR"
  sudo chown "$USER" "$EXPORTER_DIR"
fi

log "creating the demo client identity '$DEMO_CLIENT' (unsafe drivers allowed, insecure TLS)"
# Idempotent: delete any prior demo client (CR + local config) so a re-run always yields fresh,
# working credentials.
jmp admin delete client "$DEMO_CLIENT" -n "$NS" --context "$KCTX" --delete >/dev/null 2>&1 \
  && warn "client '$DEMO_CLIENT' existed — recreated with fresh credentials" || true
jmp admin create client "$DEMO_CLIENT" -n "$NS" --context "$KCTX" --unsafe -k --save >/dev/null
jmp config client use "$DEMO_CLIENT" >/dev/null 2>&1 || true

log "creating per-act exporter configs"
write_exporter demo-qemu     "example.com/dut=qemu"     "$DEMO_DIR/act1-python-qemu/exporter.yaml"
write_exporter demo-mock     "example.com/dut=mock"     "$DEMO_DIR/act2-kotlin-python/exporter.yaml"
write_exporter demo-polyglot "example.com/dut=polyglot" "$DEMO_DIR/act3-polyglot/exporter.yaml"
write_exporter demo-compat   "example.com/dut=compat"   "$DEMO_DIR/act4-backcompat/exporter.yaml"

echo
log "bring-up complete."
echo "  Controller image:  $(kubectl --context "$KCTX" -n "$NS" get jumpstarter jumpstarter -o jsonpath='{.spec.controller.image}' 2>/dev/null || echo '?')"
echo "  Pods:              kubectl -n $NS get pods"
echo "  Client:            $DEMO_CLIENT   Exporters: demo-qemu demo-mock demo-polyglot demo-compat"
echo
echo "Next: activate the venv, then run each act:"
echo "  source $REPO_ROOT/python/.venv/bin/activate"
echo "  see act1-python-qemu/README.md ... act4-backcompat/README.md"
