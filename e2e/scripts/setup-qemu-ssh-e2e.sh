#!/usr/bin/env bash
# Setup SSH + Podman on the CI runner for qemu-ssh E2E tests.
#
# Prepares the local host as a "remote" target that the
# exporter-set-controller (running inside Kind) can reach via SSH:
#   1. Generate an ed25519 SSH key
#   2. Install it for root (Deploy writes /etc/containers/systemd)
#   3. Ensure sshd is running and listens on all interfaces
#   4. Install Podman (rootful) if missing
#   5. Load exporter + qemu-runtime images into Podman
#   6. Detect the host IP reachable from Kind pods
#   7. Create the K8s Secret with the SSH private key
#   8. Enable qemu-ssh.jumpstarter.dev in the Jumpstarter CR
#   9. Render the arch-aware manifest with the detected host IP
#
# Usage:
#   bash e2e/scripts/setup-qemu-ssh-e2e.sh           # setup
#   bash e2e/scripts/setup-qemu-ssh-e2e.sh --cleanup # tear down host resources
#
# Writes state to .e2e/qemu-ssh.env for the Ginkgo test to consume.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

NS="${E2E_TEST_NS:-jumpstarter-lab}"
SSH_KEY_DIR="$REPO_ROOT/.e2e/ssh"
SSH_KEY_PATH="$SSH_KEY_DIR/id_ed25519"
ENV_FILE="$REPO_ROOT/.e2e/qemu-ssh.env"
RENDERED_MANIFEST="$REPO_ROOT/.e2e/exporterset-qemu-ssh-kind.yaml"
TEMPLATE_MANIFEST="$REPO_ROOT/e2e/manifests/exporterset-qemu-ssh-kind.yaml"

log_info()  { echo -e "\033[0;32m[INFO]\033[0m $*"; }
log_warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

# ── Guest arch ───────────────────────────────────────────────────────
detect_arch() {
    local raw="${JUMPSTARTER_E2E_QEMU_ARCH:-$(uname -m)}"
    case "${raw}" in
        x86_64|amd64)  GUEST_ARCH="x86_64" ;;
        aarch64|arm64) GUEST_ARCH="aarch64" ;;
        *)
            log_error "Unsupported arch: ${raw} (set JUMPSTARTER_E2E_QEMU_ARCH)"
            exit 1
            ;;
    esac
    log_info "Guest arch: ${GUEST_ARCH}"
}

# ── Host IP reachable from Kind pods ─────────────────────────────────
detect_host_ip() {
    # Prefer the kind network gateway (how pods reach the Docker host).
    if command -v docker &>/dev/null; then
        HOST_IP=$(docker network inspect kind \
            -f '{{range .IPAM.Config}}{{if .Gateway}}{{.Gateway}}{{end}}{{end}}' \
            2>/dev/null || true)
    fi

    # Fallback: default docker bridge gateway.
    if [ -z "${HOST_IP:-}" ] && command -v docker &>/dev/null; then
        HOST_IP=$(docker network inspect bridge \
            -f '{{range .IPAM.Config}}{{if .Gateway}}{{.Gateway}}{{end}}{{end}}' \
            2>/dev/null || true)
    fi

    # Fallback: default route on the host.
    if [ -z "${HOST_IP:-}" ]; then
        HOST_IP=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}' || true)
    fi

    if [ -z "${HOST_IP:-}" ]; then
        log_error "Could not detect host IP reachable from Kind"
        exit 1
    fi
    log_info "Host IP (from Kind): ${HOST_IP}"
}

# ── SSH key + root authorized_keys ───────────────────────────────────
setup_ssh_key() {
    log_info "Generating SSH key pair for qemu-ssh e2e..."
    mkdir -p "$SSH_KEY_DIR"
    if [ ! -f "$SSH_KEY_PATH" ]; then
        ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "e2e-qemu-ssh"
    else
        log_info "SSH key already exists at $SSH_KEY_PATH"
    fi

    # Deploy needs root: it writes /etc/containers/systemd and runs systemctl.
    local pubkey
    pubkey=$(cat "${SSH_KEY_PATH}.pub")

    sudo mkdir -p /root/.ssh
    sudo chmod 700 /root/.ssh
    if ! sudo grep -qF "$pubkey" /root/.ssh/authorized_keys 2>/dev/null; then
        echo "$pubkey" | sudo tee -a /root/.ssh/authorized_keys >/dev/null
        sudo chmod 600 /root/.ssh/authorized_keys
        log_info "Public key added to /root/.ssh/authorized_keys"
    else
        log_info "Public key already in /root/.ssh/authorized_keys"
    fi
}

ensure_sshd() {
    log_info "Ensuring sshd is running..."
    if ! systemctl is-active --quiet sshd 2>/dev/null && ! systemctl is-active --quiet ssh 2>/dev/null; then
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq
            sudo apt-get install -y -qq openssh-server
        fi
        sudo systemctl enable --now ssh 2>/dev/null || sudo systemctl enable --now sshd 2>/dev/null
    fi

    # Allow root key login (needed for Deploy).
    if [ -f /etc/ssh/sshd_config ]; then
        if ! sudo grep -qE '^\s*PermitRootLogin\s+(yes|prohibit-password|without-password)' /etc/ssh/sshd_config; then
            echo "PermitRootLogin prohibit-password" | sudo tee /etc/ssh/sshd_config.d/99-e2e-qemu-ssh.conf >/dev/null
            sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true
        fi
    fi
    log_info "sshd ready"
}

verify_ssh() {
    log_info "Verifying SSH to root@127.0.0.1..."
    ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "$SSH_KEY_PATH" root@127.0.0.1 echo "SSH OK" \
        || { log_error "SSH to root@127.0.0.1 failed"; exit 1; }

    # Also verify via the Kind-reachable host IP (what the controller will use).
    log_info "Verifying SSH to root@${HOST_IP}..."
    ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
        -i "$SSH_KEY_PATH" "root@${HOST_IP}" echo "SSH OK" \
        || { log_error "SSH to root@${HOST_IP} failed (Kind pods cannot reach host sshd)"; exit 1; }
    log_info "SSH OK"
}

# ── Podman ───────────────────────────────────────────────────────────
ensure_podman() {
    if command -v podman &>/dev/null; then
        log_info "Podman already installed: $(podman --version)"
    else
        log_info "Installing Podman..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq
            sudo apt-get install -y -qq podman
        else
            log_error "Cannot install Podman: unsupported package manager"
            exit 1
        fi
        log_info "Podman installed: $(podman --version)"
    fi

    # System quadlets need rootful podman.
    if ! sudo podman info &>/dev/null; then
        log_error "Rootful podman is not usable (sudo podman info failed)"
        exit 1
    fi
}

load_podman_images() {
    log_info "Loading container images into Podman..."

    local loaded=0

    # Prefer CI artifact paths, then local /tmp fallbacks.
    for pair in \
        "exporter:/tmp/artifacts/exporter-image.tar:/tmp/exporter-image.tar" \
        "qemu-runtime:/tmp/artifacts/qemu-runtime-image.tar:/tmp/qemu-runtime-image.tar"
    do
        IFS=: read -r name path_ci path_local <<<"$pair"
        local tarfile=""
        if [ -f "$path_ci" ]; then
            tarfile="$path_ci"
        elif [ -f "$path_local" ]; then
            tarfile="$path_local"
        fi

        if [ -n "$tarfile" ]; then
            sudo podman load -i "$tarfile"
            log_info "Loaded $name from $tarfile"
            loaded=$((loaded + 1))
        else
            # Fall back to docker→podman if docker already has the image.
            local docker_ref=""
            case "$name" in
                exporter) docker_ref="quay.io/jumpstarter-dev/jumpstarter:latest" ;;
                qemu-runtime) docker_ref="quay.io/jumpstarter-dev/virtual/qemu-runtime:latest" ;;
            esac
            if command -v docker &>/dev/null && docker image inspect "$docker_ref" &>/dev/null; then
                docker save "$docker_ref" | sudo podman load
                log_info "Loaded $name from local docker image $docker_ref"
                loaded=$((loaded + 1))
            else
                if [ -n "${CI:-}" ]; then
                    log_error "Missing image for $name (looked in $path_ci and $path_local)"
                    exit 1
                fi
                log_warn "No image tarball for $name; Podman must already have $docker_ref"
            fi
        fi
    done

    log_info "Loaded $loaded image(s) into Podman"
}

# ── K8s Secret + provisioner ─────────────────────────────────────────
create_ssh_secret() {
    log_info "Creating SSH credentials Secret in namespace $NS..."
    kubectl -n "$NS" create secret generic e2e-qemu-ssh-key \
        --from-file=ssh-privatekey="$SSH_KEY_PATH" \
        --dry-run=client -o yaml | kubectl apply -f -
    log_info "Secret e2e-qemu-ssh-key created/updated"
}

enable_provisioner() {
    log_info "Enabling qemu-ssh.jumpstarter.dev provisioner..."

    local current
    current=$(kubectl -n "$NS" get jumpstarter jumpstarter \
        -o jsonpath='{.spec.exporterSets.provisioners}' 2>/dev/null || echo "[]")

    if echo "$current" | grep -q "qemu-ssh.jumpstarter.dev"; then
        log_info "qemu-ssh provisioner already enabled"
    else
        kubectl -n "$NS" patch jumpstarter jumpstarter --type=json \
            -p '[{"op":"add","path":"/spec/exporterSets/provisioners/-","value":{"name":"qemu-ssh.jumpstarter.dev","enabled":true}}]'
    fi

    log_info "Waiting for qemu-ssh exporterset-controller deployment..."
    kubectl -n "$NS" wait --timeout=180s --for=condition=Available \
        deployment -l provisioner=qemu-ssh-jumpstarter-dev
}

# ── Render manifest ──────────────────────────────────────────────────
render_manifest() {
    log_info "Rendering manifest → $RENDERED_MANIFEST"
    mkdir -p "$(dirname "$RENDERED_MANIFEST")"

    # MEM is arch-agnostic tiny size; aarch64 firmware paths come from enrichment.
    sed \
        -e "s/__HOST_IP__/${HOST_IP}/g" \
        -e "s/__GUEST_ARCH__/${GUEST_ARCH}/g" \
        "$TEMPLATE_MANIFEST" > "$RENDERED_MANIFEST"

    log_info "Rendered manifest:"
    cat "$RENDERED_MANIFEST"
}

write_env() {
    mkdir -p "$(dirname "$ENV_FILE")"
    cat > "$ENV_FILE" <<EOF
HOST_IP=${HOST_IP}
GUEST_ARCH=${GUEST_ARCH}
SSH_KEY_PATH=${SSH_KEY_PATH}
RENDERED_MANIFEST=${RENDERED_MANIFEST}
SSH_USER=root
EOF
    log_info "Wrote $ENV_FILE"
}

# ── Cleanup host-side leftovers ──────────────────────────────────────
cleanup() {
    log_info "Cleaning up qemu-ssh e2e host resources..."

    # Stop/remove any remaining e2e exporter units and containers.
    local units
    units=$(systemctl list-units --type=service --all --no-legend 'qemu-ssh-e2e-*.service' 2>/dev/null \
        | awk '{print $1}' || true)
    if [ -n "$units" ]; then
        # shellcheck disable=SC2086
        sudo systemctl stop $units 2>/dev/null || true
    fi

    for f in /etc/containers/systemd/qemu-ssh-e2e-*.container; do
        [ -e "$f" ] || continue
        sudo rm -f "$f"
    done
    for f in /etc/jumpstarter/exporters/qemu-ssh-e2e-*.yaml; do
        [ -e "$f" ] || continue
        sudo rm -f "$f"
    done
    sudo systemctl daemon-reload 2>/dev/null || true

    # Remove leftover podman containers/volumes matching the e2e prefix.
    local containers
    containers=$(sudo podman ps -aq --filter name=qemu-ssh-e2e 2>/dev/null || true)
    if [ -n "$containers" ]; then
        # shellcheck disable=SC2086
        sudo podman rm -f $containers 2>/dev/null || true
    fi
    local volumes
    volumes=$(sudo podman volume ls -q --filter name=jumpstarter-qemu-ssh-e2e 2>/dev/null || true)
    if [ -n "$volumes" ]; then
        # shellcheck disable=SC2086
        sudo podman volume rm -f $volumes 2>/dev/null || true
    fi

    log_info "Host cleanup done"
}

# ── Main ──────────────────────────────────────────────────────────────
main() {
    if [[ "${1:-}" == "--cleanup" ]]; then
        cleanup
        exit 0
    fi

    log_info "=== Setting up qemu-ssh E2E environment ==="

    detect_arch
    detect_host_ip
    setup_ssh_key
    ensure_sshd
    verify_ssh
    ensure_podman
    load_podman_images
    create_ssh_secret
    enable_provisioner
    render_manifest
    write_env

    log_info ""
    log_info "✓ qemu-ssh E2E setup complete"
    log_info "  SSH key:  $SSH_KEY_PATH"
    log_info "  SSH user: root"
    log_info "  Host IP:  $HOST_IP"
    log_info "  Arch:     $GUEST_ARCH"
    log_info "  Manifest: $RENDERED_MANIFEST"
}

main "$@"
