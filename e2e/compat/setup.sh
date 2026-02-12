#!/usr/bin/env bash
# Jumpstarter Compatibility E2E Testing Setup Script
# This script sets up the environment for cross-version compatibility tests.
#
# No OIDC/dex is needed — tests use legacy auth (--unsafe --save).
# Uses helm directly (no operator) for simplicity.
#
# Environment variables:
#   COMPAT_SCENARIO      - "old-controller" or "old-client" (required)
#   COMPAT_CONTROLLER_TAG - Controller image tag for old-controller scenario (default: v0.7.1)
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

# Always use helm for compat tests (simpler, direct control)
export METHOD="helm"

# Scenario configuration
COMPAT_SCENARIO="${COMPAT_SCENARIO:-old-controller}"
COMPAT_CONTROLLER_TAG="${COMPAT_CONTROLLER_TAG:-v0.7.0}"
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

# Check if bats libraries are available
check_bats_libraries() {
    if ! command -v bats &> /dev/null; then
        return 1
    fi

    if ! bats --version &> /dev/null; then
        return 1
    fi

    local test_file=$(mktemp)
    cat > "$test_file" <<'EOF'
setup() {
  bats_load_library bats-support
  bats_load_library bats-assert
}

@test "dummy" {
  run echo "test"
  assert_success
}
EOF

    if bats "$test_file" &> /dev/null; then
        rm -f "$test_file"
        return 0
    else
        rm -f "$test_file"
        return 1
    fi
}

# Install bats libraries locally
install_bats_libraries_local() {
    local LIB_DIR="$REPO_ROOT/.bats/lib"
    local ORIGINAL_DIR="$PWD"

    log_info "Installing bats helper libraries to $LIB_DIR..."

    mkdir -p "$LIB_DIR"
    cd "$LIB_DIR"

    if [ ! -d "bats-support" ]; then
        log_info "Cloning bats-support..."
        git clone --depth 1 https://github.com/bats-core/bats-support.git
    else
        log_info "bats-support already installed"
    fi

    if [ ! -d "bats-assert" ]; then
        log_info "Cloning bats-assert..."
        git clone --depth 1 https://github.com/bats-core/bats-assert.git
    else
        log_info "bats-assert already installed"
    fi

    if [ ! -d "bats-file" ]; then
        log_info "Cloning bats-file..."
        git clone --depth 1 https://github.com/bats-core/bats-file.git
    else
        log_info "bats-file already installed"
    fi

    cd "$ORIGINAL_DIR"

    export BATS_LIB_PATH="$LIB_DIR:${BATS_LIB_PATH:-}"

    log_info "Bats libraries installed successfully"

    if check_bats_libraries; then
        log_info "Libraries verified and working"
    else
        log_error "Libraries installed but verification failed"
        exit 1
    fi
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

    if ! command -v bats &> /dev/null; then
        log_info "Installing bats..."
        if is_ci; then
            sudo apt-get update
            sudo apt-get install -y bats
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            log_info "Installing bats-core via Homebrew..."
            brew install bats-core
        else
            log_error "bats not found. Please install it manually."
            exit 1
        fi
    fi

    if ! check_bats_libraries; then
        log_info "Installing bats libraries locally..."
        install_bats_libraries_local
    else
        log_info "Bats libraries are already available"
        export BATS_LIB_PATH="$REPO_ROOT/.bats/lib:${BATS_LIB_PATH:-}"
    fi

    log_info "Dependencies installed"
}

# Get the external IP for baseDomain
get_external_ip() {
    if which ip 2>/dev/null 1>/dev/null; then
        ip route get 1.1.1.1 | grep -oP 'src \K\S+'
    else
        local INTERFACE
        INTERFACE=$(route get 1.1.1.1 | grep interface | awk '{print $2}')
        ifconfig | grep "$INTERFACE" -A 10 | grep "inet " | grep -Fv 127.0.0.1 | awk '{print $2}' | head -n 1
    fi
}

# Create kind cluster and install grpcurl
create_cluster() {
    log_info "Creating kind cluster..."

    cd "$REPO_ROOT"
    make -C controller cluster grpcurl

    log_info "Kind cluster created"
}

# Deploy old controller using the OCI helm chart from quay.io
deploy_old_controller() {
    log_info "Deploying old controller (version: $COMPAT_CONTROLLER_TAG)..."

    cd "$REPO_ROOT"

    # Strip leading 'v' for helm version (v0.7.1 -> 0.7.1)
    local HELM_VERSION="${COMPAT_CONTROLLER_TAG#v}"

    # Compute networking variables
    local IP
    IP=$(get_external_ip)
    BASEDOMAIN="jumpstarter.${IP}.nip.io"
    GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
    GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"

    kubectl config use-context kind-jumpstarter

    # Install old controller from OCI helm chart
    log_info "Installing old controller via helm (version: ${HELM_VERSION})..."
    helm install --namespace jumpstarter-lab \
        --create-namespace \
        --set global.baseDomain="${BASEDOMAIN}" \
        --set jumpstarter-controller.grpc.endpoint="${GRPC_ENDPOINT}" \
        --set jumpstarter-controller.grpc.routerEndpoint="${GRPC_ROUTER_ENDPOINT}" \
        --set jumpstarter-controller.grpc.nodeport.enabled=true \
        --set jumpstarter-controller.grpc.mode=nodeport \
        --set global.metrics.enabled=false \
        --version="${HELM_VERSION}" \
        jumpstarter oci://quay.io/jumpstarter-dev/helm/jumpstarter

    kubectl config set-context --current --namespace=jumpstarter-lab

    # Wait for gRPC endpoints
    local GRPCURL="${REPO_ROOT}/controller/bin/grpcurl"
    log_info "Waiting for gRPC endpoints..."
    for ep in ${GRPC_ENDPOINT} ${GRPC_ROUTER_ENDPOINT}; do
        local retries=60
        log_info "  Checking ${ep}..."
        while ! ${GRPCURL} -insecure "${ep}" list > /dev/null 2>&1; do
            sleep 2
            retries=$((retries - 1))
            if [ ${retries} -eq 0 ]; then
                log_error "${ep} not ready after 120s"
                exit 1
            fi
        done
    done

    log_info "Old controller deployed"
}

# Deploy new controller from HEAD
deploy_new_controller() {
    log_info "Deploying new controller from HEAD..."

    cd "$REPO_ROOT"

    # Build image from HEAD and deploy with helm (no OIDC, no extra values)
    make -C controller docker-build
    cd controller
    ./hack/deploy_with_helm.sh
    cd "$REPO_ROOT"

    log_info "New controller deployed"
}

# Install jumpstarter Python packages from HEAD
install_jumpstarter() {
    log_info "Installing jumpstarter from HEAD..."

    cd "$REPO_ROOT"
    cd python
    make sync
    cd ..

    log_info "Jumpstarter python installed"
}

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

    # Get the controller endpoint from helm values
    export ENDPOINT
    ENDPOINT=$(helm get values jumpstarter --output json | jq -r '."jumpstarter-controller".grpc.endpoint')

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
JS_NAMESPACE=$JS_NAMESPACE
REPO_ROOT=$REPO_ROOT
SCRIPT_DIR=$SCRIPT_DIR
METHOD=$METHOD
BATS_LIB_PATH=${BATS_LIB_PATH:-}
EOF

    # Add OLD_JMP if set (old-client scenario)
    if [ -n "${OLD_JMP:-}" ]; then
        echo "OLD_JMP=$OLD_JMP" >> "$REPO_ROOT/.e2e-setup-complete"
    fi

    log_info "Test environment ready"
}

# Main execution
main() {
    log_info "=== Jumpstarter Compatibility E2E Setup ==="
    log_info "Scenario: $COMPAT_SCENARIO"
    log_info "Namespace: $JS_NAMESPACE"
    log_info "Repository Root: $REPO_ROOT"
    echo ""

    install_dependencies
    echo ""

    create_cluster
    echo ""

    case "$COMPAT_SCENARIO" in
        old-controller)
            log_info "Scenario: Old Controller ($COMPAT_CONTROLLER_TAG) + New Client/Exporter"
            deploy_old_controller
            echo ""
            install_jumpstarter
            ;;
        old-client)
            log_info "Scenario: New Controller + Old Client/Exporter ($COMPAT_CLIENT_VERSION)"
            deploy_new_controller
            echo ""
            install_jumpstarter
            echo ""
            install_old_client
            ;;
        *)
            log_error "Unknown COMPAT_SCENARIO: $COMPAT_SCENARIO (expected 'old-controller' or 'old-client')"
            exit 1
            ;;
    esac
    echo ""

    setup_test_environment
    echo ""

    log_info "=== Compat setup complete! ==="
    log_info "Scenario: $COMPAT_SCENARIO"
    log_info "To run tests: make e2e-compat-run COMPAT_TEST=<old-controller|old-client>"
}

main "$@"
