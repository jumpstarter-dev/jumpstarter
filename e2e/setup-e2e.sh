#!/usr/bin/env bash
# Jumpstarter End-to-End Testing Setup Script
# This script performs one-time setup for e2e testing

set -euo pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get the monorepo root (parent of e2e directory)
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Default namespace for tests
export JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

# Deployment method: operator (default) or helm
export METHOD="${METHOD:-operator}"

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
    
    # Try to load the libraries
    if ! bats --version &> /dev/null; then
        return 1
    fi
    
    # Check if libraries can be loaded by testing with a simple script
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
    
    # Run test with current BATS_LIB_PATH
    if bats "$test_file" &> /dev/null; then
        rm -f "$test_file"
        return 0
    else
        rm -f "$test_file"
        return 1
    fi
}

# Install bats libraries locally (works on all systems)
install_bats_libraries_local() {
    local LIB_DIR="$REPO_ROOT/.bats/lib"
    local ORIGINAL_DIR="$PWD"
    
    log_info "Installing bats helper libraries to $LIB_DIR..."
    
    mkdir -p "$LIB_DIR"
    cd "$LIB_DIR"
    
    # Install bats-support
    if [ ! -d "bats-support" ]; then
        log_info "Cloning bats-support..."
        git clone --depth 1 https://github.com/bats-core/bats-support.git
    else
        log_info "bats-support already installed"
    fi
    
    # Install bats-assert
    if [ ! -d "bats-assert" ]; then
        log_info "Cloning bats-assert..."
        git clone --depth 1 https://github.com/bats-core/bats-assert.git
    else
        log_info "bats-assert already installed"
    fi
    
    # Install bats-file
    if [ ! -d "bats-file" ]; then
        log_info "Cloning bats-file..."
        git clone --depth 1 https://github.com/bats-core/bats-file.git
    else
        log_info "bats-file already installed"
    fi
    
    cd "$ORIGINAL_DIR"
    
    # Set BATS_LIB_PATH
    export BATS_LIB_PATH="$LIB_DIR:${BATS_LIB_PATH:-}"
    
    log_info "✓ Bats libraries installed successfully"
    log_info "BATS_LIB_PATH set to: $BATS_LIB_PATH"
    
    # Verify installation worked
    if check_bats_libraries; then
        log_info "✓ Libraries verified and working"
    else
        log_error "Libraries installed but verification failed"
        log_error "Please check that the following directories exist:"
        log_error "  $LIB_DIR/bats-support"
        log_error "  $LIB_DIR/bats-assert"
        exit 1
    fi
}

# Step 1: Install dependencies
install_dependencies() {
    log_info "Installing dependencies..."
    
    # Install uv if not already installed
    if ! command -v uv &> /dev/null; then
        log_info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    
    # Install Python 3.12
    log_info "Installing Python 3.12..."
    uv python install 3.12
    
    # Install bats if not already installed
    if ! command -v bats &> /dev/null; then
        log_info "Installing bats..."
        if is_ci; then
            sudo apt-get update
            sudo apt-get install -y bats
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            log_info "Installing bats-core via Homebrew..."
            brew install bats-core
        else
            log_error "bats not found. Please install it manually:"
            log_error "  Ubuntu/Debian: sudo apt-get install bats"
            log_error "  Fedora/RHEL: sudo dnf install bats"
            log_error "  macOS: brew install bats-core"
            exit 1
        fi
    fi
    
    # Always install bats libraries locally for consistency across all systems
    # This ensures libraries work regardless of package manager or distribution
    if ! check_bats_libraries; then
        log_info "Installing bats libraries locally..."
        install_bats_libraries_local
    else
        log_info "✓ Bats libraries are already available"
        # Still set BATS_LIB_PATH to include local directory for consistency
        export BATS_LIB_PATH="$REPO_ROOT/.bats/lib:${BATS_LIB_PATH:-}"
    fi
    
    log_info "✓ Dependencies installed"
}

# Step 2: Deploy dex
deploy_dex() {
    log_info "Deploying dex..."
    
    cd "$REPO_ROOT"
    
    # Generate certificates
    log_info "Generating certificates..."
    go run github.com/cloudflare/cfssl/cmd/cfssl@latest gencert -initca "$SCRIPT_DIR"/ca-csr.json | \
        go run github.com/cloudflare/cfssl/cmd/cfssljson@latest -bare ca -
    go run github.com/cloudflare/cfssl/cmd/cfssl@latest gencert -ca=ca.pem -ca-key=ca-key.pem \
        -config="$SCRIPT_DIR"/ca-config.json -profile=www "$SCRIPT_DIR"/dex-csr.json | \
        go run github.com/cloudflare/cfssl/cmd/cfssljson@latest -bare server
    

    make -C controller cluster
    
    # Create dex namespace and TLS secret
    log_info "Creating dex namespace and secrets..."
    kubectl create namespace dex
    kubectl -n dex create secret tls dex-tls \
        --cert=server.pem \
        --key=server-key.pem
    
    # Create .e2e directory for configuration files
    log_info "Creating .e2e directory for local configuration..."
    mkdir -p "$REPO_ROOT/.e2e"
    
    # Copy values.kind.yaml to .e2e and inject the CA certificate
    log_info "Creating values file with CA certificate..."
    cp "$SCRIPT_DIR"/values.kind.yaml "$REPO_ROOT/.e2e/values.kind.yaml"
    
    log_info "Injecting CA certificate into values..."
    go run github.com/mikefarah/yq/v4@latest -i \
        '.jumpstarter-controller.config.authentication.jwt[0].issuer.certificateAuthority = load_str("ca.pem")' \
        "$REPO_ROOT/.e2e/values.kind.yaml"
    
    log_info "✓ Values file with CA certificate created at .e2e/values.kind.yaml"
    
    # Create OIDC reviewer binding (important!)
    log_info "Creating OIDC reviewer cluster role binding..."
    kubectl create clusterrolebinding oidc-reviewer \
        --clusterrole=system:service-account-issuer-discovery \
        --group=system:unauthenticated
    
    # Install dex via helm
    log_info "Installing dex via helm..."
    helm repo add dex https://charts.dexidp.io
    helm install --namespace dex --wait -f "$SCRIPT_DIR"/dex.values.yaml dex dex/dex
    
    # Install CA certificate
    log_info "Installing CA certificate..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # this may be unnecessary, but keeping it here for now
        #log_warn "About to add the CA certificate to your macOS login keychain"
        #security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db ca.pem
        #log_info "✓ CA certificate added to macOS login keychain"
        true
    else
        log_warn "About to install the CA certificate system-wide (requires sudo)"
        # Detect if this is a RHEL/Fedora system or Debian/Ubuntu system
        if [ -d "/etc/pki/ca-trust/source/anchors" ]; then
            # RHEL/Fedora/CentOS
            sudo cp ca.pem /etc/pki/ca-trust/source/anchors/dex.crt
            sudo update-ca-trust
            log_info "✓ CA certificate installed system-wide (RHEL/Fedora)"
        else
            # Debian/Ubuntu
            sudo cp ca.pem /usr/local/share/ca-certificates/dex.crt
            sudo update-ca-certificates
            log_info "✓ CA certificate installed system-wide (Debian/Ubuntu)"
        fi
    fi
    
    # Add dex to /etc/hosts if not already present
    log_info "Checking /etc/hosts for dex entry..."
    if ! grep -q "dex.dex.svc.cluster.local" /etc/hosts 2>/dev/null; then
        log_warn "About to add 'dex.dex.svc.cluster.local' to /etc/hosts (requires sudo)"
        echo "127.0.0.1 dex.dex.svc.cluster.local" | sudo tee -a /etc/hosts
        log_info "✓ Added dex to /etc/hosts"
    else
        log_info "✓ dex.dex.svc.cluster.local already in /etc/hosts"
    fi
    
    log_info "✓ Dex deployed"
}

# Step 3: Deploy jumpstarter controller
deploy_controller() {
    log_info "Deploying jumpstarter controller (method: $METHOD)..."
    
    cd "$REPO_ROOT"
    
    # Validate METHOD
    if [ "$METHOD" != "operator" ] && [ "$METHOD" != "helm" ]; then
        log_error "Unknown deployment method: $METHOD (expected 'operator' or 'helm')"
        exit 1
    fi
    
    # Deploy with CA certificate
    log_info "Deploying controller with CA certificate using $METHOD..."
    if [ "$METHOD" = "operator" ]; then
        # For operator: use OPERATOR_USE_DEX to inject dex config directly
        OPERATOR_USE_DEX=true DEX_CA_FILE="$REPO_ROOT/ca.pem" METHOD=$METHOD make -C controller deploy
    else
        # For helm: use EXTRA_VALUES to pass the values file
        EXTRA_VALUES="--values $REPO_ROOT/.e2e/values.kind.yaml" METHOD=$METHOD make -C controller deploy
    fi
    
    log_info "✓ Controller deployed"
}

# Step 4: Install jumpstarter
install_jumpstarter() {
    log_info "Installing jumpstarter..."
    
    cd "$REPO_ROOT"
    cd python
    make sync
    cd ..
    log_info "✓ Jumpstarter python installed"
}

# Step 5: Setup test environment
setup_test_environment() {
    log_info "Setting up test environment..."
    
    cd "$REPO_ROOT"
    
    # Get the controller endpoint based on deployment method
    # Note: We declare BASEDOMAIN separately from assignment so that command
    # failures propagate under set -e (local VAR=$(...) masks exit codes).
    local BASEDOMAIN
    if [ "$METHOD" = "operator" ]; then
        # For operator deployment, construct the endpoint from the Jumpstarter CR
        # The operator uses nodeport mode by default with port 8082
        BASEDOMAIN=$(kubectl get jumpstarter -n "${JS_NAMESPACE}" jumpstarter -o jsonpath='{.spec.baseDomain}')
        export ENDPOINT="grpc.${BASEDOMAIN}:8082"
        export LOGIN_ENDPOINT="login.${BASEDOMAIN}:8086"
    else
        # For helm deployment, get the endpoint from helm values
        export ENDPOINT=$(helm get values jumpstarter --output json | jq -r '."jumpstarter-controller".grpc.endpoint')
        # Login endpoint is on nodeport 30014 mapped to host port 8086
        BASEDOMAIN=$(helm get values jumpstarter --output json | jq -r '.global.baseDomain')
        export LOGIN_ENDPOINT="login.${BASEDOMAIN}:8086"
    fi
    log_info "Controller endpoint: $ENDPOINT"
    log_info "Login endpoint: $LOGIN_ENDPOINT"
    
    # Setup exporters directory (only use sudo if needed)
    if [ ! -d /etc/jumpstarter/exporters ] || [ ! -w /etc/jumpstarter/exporters ]; then
        log_info "Setting up exporters directory in /etc/jumpstarter/exporters (requires sudo)..."
        sudo mkdir -p /etc/jumpstarter/exporters
        sudo chown "$USER" /etc/jumpstarter/exporters
    else
        log_info "Exporters directory already exists and is writable"
    fi
    
    # Create service accounts
    log_info "Creating service accounts..."
    kubectl create -n "${JS_NAMESPACE}" sa test-client-sa
    kubectl create -n "${JS_NAMESPACE}" sa test-exporter-sa
    
    # Create a marker file to indicate setup is complete
    echo "ENDPOINT=$ENDPOINT" > "$REPO_ROOT/.e2e-setup-complete"
    echo "LOGIN_ENDPOINT=$LOGIN_ENDPOINT" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "JS_NAMESPACE=$JS_NAMESPACE" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REPO_ROOT=$REPO_ROOT" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "SCRIPT_DIR=$SCRIPT_DIR" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "METHOD=$METHOD" >> "$REPO_ROOT/.e2e-setup-complete"
    
    # Set SSL certificate paths for Python to use the generated CA
    echo "SSL_CERT_FILE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REQUESTS_CA_BUNDLE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    
    # Save BATS_LIB_PATH for test runs
    echo "BATS_LIB_PATH=$BATS_LIB_PATH" >> "$REPO_ROOT/.e2e-setup-complete"
    
    log_info "✓ Test environment ready"
}

# Main execution
main() {
    log_info "=== Jumpstarter E2E Setup ==="
    log_info "Namespace: $JS_NAMESPACE"
    log_info "Deployment Method: $METHOD"
    log_info "Repository Root: $REPO_ROOT"
    log_info "Script Directory: $SCRIPT_DIR"
    echo ""
    
    install_dependencies
    echo ""
    
    deploy_dex
    echo ""
    
    deploy_controller
    echo ""
    
    install_jumpstarter
    echo ""
    
    setup_test_environment
    echo ""
    
    log_info "✓✓✓ Setup complete! ✓✓✓"
    log_info ""
    log_info "To run tests:"
    log_info "  cd $REPO_ROOT"
    log_info "  bash e2e/run-e2e.sh"
    log_info ""
    log_info "Or use the Makefile:"
    log_info "  make e2e"
}

# Run main function
main "$@"
