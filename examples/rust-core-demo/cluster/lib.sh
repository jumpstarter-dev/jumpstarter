#!/usr/bin/env bash
# Shared helpers for the rust-core-demo bring-up scripts. Sourced by up.sh / down.sh
# and the per-act run scripts. Not meant to be run directly.

# Resolve the monorepo root (this file lives at examples/rust-core-demo/cluster/lib.sh).
DEMO_CLUSTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "$DEMO_CLUSTER_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEMO_DIR/../.." && pwd)"

# The demo namespace + identity names (kept together so every act agrees).
export NS="${NS:-jumpstarter-lab}"
export DEMO_CLIENT="${DEMO_CLIENT:-demo-client}"

# The kube context the jumpstarter cluster lives in. `jmp admin` and `kubectl` use the *current*
# kubeconfig context by default, which may be some other cluster — so we always target this one
# explicitly. A kind cluster named `jumpstarter` yields context `kind-jumpstarter`.
export KCTX="${KCTX:-kind-jumpstarter}"

# The Rust controller/router image built by `make rust-controller-image`.
export RUST_CONTROLLER_IMG="${RUST_CONTROLLER_IMG:-quay.io/jumpstarter-dev/jumpstarter-controller-rust:latest}"

# Where the exporter runtime configs (credentials + driver tree) are written. `jmp run
# --exporter <name>` resolves names from the user dir then here.
export EXPORTER_DIR="${EXPORTER_DIR:-/etc/jumpstarter/exporters}"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[demo]${NC} $*"; }
warn() { echo -e "${YELLOW}[demo]${NC} $*"; }
err()  { echo -e "${RED}[demo]${NC} $*" >&2; }

# The python interpreter from the synced jumpstarter venv (has jmp/j on PATH beside it,
# plus PyYAML for config splicing). Falls back to `python3` before `make sync` has run.
venv_python() {
  if [ -x "$REPO_ROOT/python/.venv/bin/python" ]; then
    echo "$REPO_ROOT/python/.venv/bin/python"
  else
    command -v python3
  fi
}

# Put the venv bin dir (jmp, j, jumpstarter-exporter-host shims once installed) on PATH.
demo_activate_venv() {
  if [ -d "$REPO_ROOT/python/.venv/bin" ]; then
    export PATH="$REPO_ROOT/python/.venv/bin:$PATH"
  fi
}

require() {
  command -v "$1" >/dev/null 2>&1 || { err "required tool '$1' not found on PATH"; return 1; }
}

# write_exporter <name> <label> <act_config_yaml> — create an Exporter CR + credentials in
# the cluster, then splice this act's driver tree (its `export:` block) onto the credentialed
# config and drop the result in $EXPORTER_DIR/<name>.yaml. `-k` bakes tls.insecure=true so the
# self-signed controller TLS is accepted without a CA install.
write_exporter() {
  local name="$1" label="$2" act_cfg="$3"
  local dest="$EXPORTER_DIR/$name.yaml"
  local tmp; tmp="$(mktemp -t "jmp-demo-$name.XXXXXX")"
  log "creating exporter identity '$name' (label: $label)"
  jmp admin create exporter "$name" -n "$NS" --context "$KCTX" -k --label "$label" --out "$tmp" >/dev/null
  "$(venv_python)" - "$tmp" "$act_cfg" "$dest" "$name" <<'PY'
import sys, yaml
creds_path, act_path, dest_path, name = sys.argv[1:5]
with open(creds_path) as f: creds = yaml.safe_load(f) or {}
with open(act_path)   as f: act   = yaml.safe_load(f) or {}
creds["export"] = act.get("export", {})
if "description" in act: creds["description"] = act["description"]
creds.setdefault("metadata", {})["name"] = name
with open(dest_path, "w") as f:
    yaml.safe_dump(creds, f, sort_keys=False)
print(dest_path)
PY
  rm -f "$tmp"
  log "wrote $dest"
}
