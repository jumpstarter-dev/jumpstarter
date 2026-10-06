#!/usr/bin/env bash
# Setup SSH + Podman on the CI runner for qemu-ssh E2E tests.
#
# This script prepares the local host as a "remote" target:
#   1. Generates an ed25519 SSH key pair
#   2. Adds the public key to authorized_keys
#   3. Ensures sshd is running
#   4. Installs Podman (if missing)
#   5. Loads exporter + qemu-runtime images into Podman
#   6. Creates the K8s Secret with the SSH private key
#   7. Enables qemu-ssh.jumpstarter.dev provisioner in the Jumpstarter CR
#
# Usage:
#   bash e2e/scripts/setup-qemu-ssh-e2e.sh
#
# Requires: ssh, podman (or apt), kubectl, Kind cluster already up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

NS="${E2E_TEST_NS:-jumpstarter-lab}"
SSH_KEY_DIR="$REPO_ROOT/.e2e/ssh"
SSH_KEY_PATH="$SSH_KEY_DIR/id_ed25519"

log_info()  { echo -e "\033[0;32m[INFO]\033[0m $*"; }
log_warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

# ── 1. Generate SSH key ──────────────────────────────────────────────
setup_ssh_key() {
    log_info "Generating SSH key pair for qemu-ssh e2e..."
    mkdir -p "$SSH_KEY_DIR"
    if [ ! -f "$SSH_KEY_PATH" ]; then
        ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "e2e-qemu-ssh"
    else
        log_info "SSH key already exists at $SSH_KEY_PATH"
    fi

    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"

    # Add to authorized_keys (idempotent)
    local pubkey
    pubkey=$(cat "${SSH_KEY_PATH}.pub")
    if ! grep -qF "$pubkey" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
        echo "$pubkey" >> "$HOME/.ssh/authorized_keys"
        chmod 600 "$HOME/.ssh/authorized_keys"
        log_info "Public key added to authorized_keys"
    else
        log_info "Public key already in authorized_keys"
    fi
}

# ── 2. Ensure sshd is running ────────────────────────────────────────
ensure_sshd() {
    log_info "Ensuring sshd is running..."
    if systemctl is-active --quiet sshd 2>/dev/null || systemctl is-active --quiet ssh 2>/dev/null; then
        log_info "sshd is already running"
        return
    fi

    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq openssh-server
    fi

    sudo systemctl enable --now ssh 2>/dev/null || sudo systemctl enable --now sshd 2>/dev/null
    log_info "sshd started"
}

# ── 3. Verify SSH to localhost works ──────────────────────────────────
verify_ssh() {
    log_info "Verifying SSH to localhost..."
    # Accept the host key automatically and verify we can connect
    ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "$SSH_KEY_PATH" "$(whoami)@127.0.0.1" echo "SSH OK" \
        || { log_error "SSH to localhost failed"; exit 1; }
    log_info "SSH to localhost works"
}

# ── 4. Install Podman ────────────────────────────────────────────────
ensure_podman() {
    if command -v podman &>/dev/null; then
        log_info "Podman already installed: $(podman --version)"
        return
    fi

    log_info "Installing Podman..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq podman
    else
        log_error "Cannot install Podman: unsupported package manager"
        exit 1
    fi
    log_info "Podman installed: $(podman --version)"
}

# ── 5. Load container images into Podman ──────────────────────────────
load_podman_images() {
    log_info "Loading container images into Podman..."

    for name in exporter qemu-runtime; do
        local tarfile="/tmp/${name}-image.tar"
        if [ -f "$tarfile" ]; then
            podman load -i "$tarfile"
            log_info "Loaded $name image into Podman"
        else
            log_warn "$tarfile not found, skipping (image must already be available)"
        fi
    done
}

# ── 6. Create K8s Secret with SSH key ─────────────────────────────────
create_ssh_secret() {
    log_info "Creating SSH credentials Secret in namespace $NS..."
    kubectl -n "$NS" create secret generic e2e-qemu-ssh-key \
        --from-file=ssh-privatekey="$SSH_KEY_PATH" \
        --dry-run=client -o yaml | kubectl apply -f -
    log_info "Secret e2e-qemu-ssh-key created/updated"
}

# ── 7. Enable qemu-ssh provisioner ───────────────────────────────────
enable_provisioner() {
    log_info "Enabling qemu-ssh.jumpstarter.dev provisioner..."

    # Patch the Jumpstarter CR to add qemu-ssh provisioner
    local current
    current=$(kubectl -n "$NS" get jumpstarter jumpstarter \
        -o jsonpath='{.spec.exporterSets.provisioners}' 2>/dev/null || echo "[]")

    if echo "$current" | grep -q "qemu-ssh.jumpstarter.dev"; then
        log_info "qemu-ssh provisioner already enabled"
        return
    fi

    kubectl -n "$NS" patch jumpstarter jumpstarter --type=json \
        -p '[{"op":"add","path":"/spec/exporterSets/provisioners/-","value":{"name":"qemu-ssh.jumpstarter.dev","enabled":true}}]'

    log_info "Waiting for qemu-ssh exporterset-controller deployment..."
    kubectl -n "$NS" wait --timeout=120s --for=condition=Available \
        deployment -l provisioner=qemu-ssh-jumpstarter-dev \
        2>/dev/null || log_warn "Deployment not yet available (may need more time)"
}

# ── Main ──────────────────────────────────────────────────────────────
main() {
    log_info "=== Setting up qemu-ssh E2E environment ==="

    setup_ssh_key
    ensure_sshd
    verify_ssh
    ensure_podman
    load_podman_images
    create_ssh_secret
    enable_provisioner

    log_info ""
    log_info "✓ qemu-ssh E2E setup complete"
    log_info "  SSH key:  $SSH_KEY_PATH"
    log_info "  User:     $(whoami)"
    log_info "  Host:     127.0.0.1"
}

main "$@"
