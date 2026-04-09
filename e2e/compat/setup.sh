#!/usr/bin/env bash
# Jumpstarter Compatibility E2E Testing Setup Script
# This script sets up the environment for cross-version compatibility tests.
#
# No OIDC/dex is needed -- tests use legacy auth (--unsafe --save).
# Uses operator-based deployment.
#
# Environment variables:
#   COMPAT_CLIENT_VERSION - PyPI version for old-client scenario (default: 0.7.1)

set -euo pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The parent e2e directory
E2E_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Get the monorepo root
REPO_ROOT="$(cd "$E2E_DIR/.." && pwd)"

# Default namespace for tests
export JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

COMPAT_CLIENT_VERSION="${COMPAT_CLIENT_VERSION:-0.7.1}"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Check if running in CI
is_ci() {
    [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]
}

# Install dependencies
install_dependencies() {
    log_info "Installing dependencies..."

    if ! command -v uv &> /dev/null; then
        log_info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    log_info "Installing Python 3.12..."
    uv python install 3.12

    log_info "Dependencies installed"
}

# Create kind cluster and install grpcurl
create_cluster() {
    log_info "Creating kind cluster..."

    cd "$REPO_ROOT"
    make -C controller cluster grpcurl

    log_info "Kind cluster created"
}

deploy_new_controller() {
    log_info "Deploying new controller from HEAD..."

    cd "$REPO_ROOT"

    make -C controller deploy

    log_info "New controller deployed"
}

# Install jumpstarter Python packages from HEAD (shared helper)
# shellcheck source=../lib/install.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/lib/install.sh"

# Install old client from PyPI into separate venv
install_old_client() {
    log_info "Installing old client v${COMPAT_CLIENT_VERSION} from PyPI..."

    local OLD_JMP_DIR="/tmp/jumpstarter-compat"

    # Clean up any previous installation
    rm -rf "$OLD_JMP_DIR"

    # Create a separate venv for old client
    uv venv "$OLD_JMP_DIR/.venv" --python 3.12

    # Install old packages from PyPI
    uv pip install --python "$OLD_JMP_DIR/.venv/bin/python" \
        "jumpstarter-cli==${COMPAT_CLIENT_VERSION}" \
        "jumpstarter==${COMPAT_CLIENT_VERSION}" \
        "jumpstarter-driver-composite==${COMPAT_CLIENT_VERSION}" \
        "jumpstarter-driver-power==${COMPAT_CLIENT_VERSION}" \
        "jumpstarter-driver-opendal==${COMPAT_CLIENT_VERSION}"

    # Verify the old jmp works
    "$OLD_JMP_DIR/.venv/bin/jmp" version || log_warn "Old jmp version command failed"

    export OLD_JMP="$OLD_JMP_DIR/.venv/bin/jmp"
    log_info "Old jmp CLI installed at: $OLD_JMP"
}

# Setup test environment
setup_test_environment() {
    log_info "Setting up test environment..."

    cd "$REPO_ROOT"

    # Get the controller endpoint from Jumpstarter CR
    export ENDPOINT
    local BASEDOMAIN
    BASEDOMAIN=$(kubectl get jumpstarter -n "${JS_NAMESPACE}" jumpstarter -o jsonpath='{.spec.baseDomain}' 2>/dev/null) || true
    if [ -z "${BASEDOMAIN}" ]; then
        log_error "Failed to get baseDomain from Jumpstarter CR in namespace ${JS_NAMESPACE}. Is the controller deployed with a Jumpstarter CR?"
        exit 1
    fi
    ENDPOINT="grpc.${BASEDOMAIN}:8082"

    log_info "Controller endpoint: $ENDPOINT"

    # Setup exporters directory
    if [ ! -d /etc/jumpstarter/exporters ] || [ ! -w /etc/jumpstarter/exporters ]; then
        log_info "Setting up exporters directory (requires sudo)..."
        sudo mkdir -p /etc/jumpstarter/exporters
        sudo chown "$USER" /etc/jumpstarter/exporters
    else
        log_info "Exporters directory already exists and is writable"
    fi

    # Write setup configuration
    cat > "$REPO_ROOT/.e2e-setup-complete" <<EOF
ENDPOINT=$ENDPOINT
E2E_TEST_NS=$JS_NAMESPACE
REPO_ROOT=$REPO_ROOT
SCRIPT_DIR=$SCRIPT_DIR
EOF

    log_info "Test environment ready"
}

# Main execution
main() {
    log_info "=== Jumpstarter Compatibility E2E Setup ==="
    log_info "Scenario: New Controller + Old Client/Exporter ($COMPAT_CLIENT_VERSION)"
    log_info "Namespace: $JS_NAMESPACE"
    log_info "Repository Root: $REPO_ROOT"
    echo ""

    install_dependencies
    echo ""

    create_cluster
    echo ""

    deploy_new_controller
    echo ""

    install_jumpstarter
    echo ""

    install_old_client
    echo ""

    setup_test_environment
    echo ""

    log_info "=== Compat setup complete! ==="
    log_info "To run tests: make e2e-compat-run COMPAT_TEST=old-client"
}

main "$@"
