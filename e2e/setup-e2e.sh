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

# Install bats libraries on macOS
install_bats_libraries_macos() {
    if ! command -v brew &> /dev/null; then
        log_error "Homebrew not found. Please install Homebrew first."
        exit 1
    fi
    
    local BREW_PREFIX=$(brew --prefix)
    local LIB_DIR="$BREW_PREFIX/lib"
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
    
    log_info "✓ Bats libraries installed successfully"
    
    # Verify installation worked
    cd "$ORIGINAL_DIR"
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
            sudo apt-get install -y bats bats-support bats-assert
        elif [[ "$OSTYPE" == "darwin"* ]]; then
            log_info "Installing bats-core via Homebrew..."
            brew install bats-core
        else
            log_error "bats not found. Please install it manually:"
            log_error "  Ubuntu/Debian: sudo apt-get install bats bats-support bats-assert"
            log_error "  macOS: brew install bats-core"
            exit 1
        fi
    fi
    
    # Check and install bats libraries if needed
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
            # Set BATS_LIB_PATH
            BREW_PREFIX=$(brew --prefix)
            export BATS_LIB_PATH="${BREW_PREFIX}/lib:${BATS_LIB_PATH:-}"
            
            # Check if libraries are accessible
            if ! check_bats_libraries; then
                log_warn "Bats libraries not found or not accessible"
                install_bats_libraries_macos
            else
                log_info "✓ Bats libraries are available"
            fi
            
            log_info "BATS_LIB_PATH set to: $BATS_LIB_PATH"
        fi
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
    
    # Setup kind cluster with custom config
    log_info "Setting up kind cluster..."
    cp "$SCRIPT_DIR"/kind_cluster.yaml ./controller/hack/kind_cluster.yaml
    make -C controller cluster
    
    # Create dex namespace and TLS secret
    log_info "Creating dex namespace and secrets..."
    kubectl create namespace dex
    kubectl -n dex create secret tls dex-tls \
        --cert=server.pem \
        --key=server-key.pem
    
    # Update values.kind.yaml with CA
    log_info "Updating controller values with CA certificate..."
    go run github.com/mikefarah/yq/v4@latest -i \
        '.jumpstarter-controller.config.authentication.jwt[0].issuer.certificateAuthority = load_str("ca.pem")' \
        "$SCRIPT_DIR"/values.kind.yaml
    
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
        sudo cp ca.pem /usr/local/share/ca-certificates/dex.crt
        sudo update-ca-certificates
        log_info "✓ CA certificate installed system-wide"
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
    log_info "Deploying jumpstarter controller..."
    
    cd "$REPO_ROOT"
    
    cp "$SCRIPT_DIR"/values.kind.yaml ./controller/deploy/helm/jumpstarter/values.kind.yaml
    make -C controller deploy
    
    log_info "✓ Controller deployed"
}

# Step 4: Install jumpstarter
install_jumpstarter() {
    log_info "Installing jumpstarter..."
    
    cd "$REPO_ROOT"
    
    # Create virtual environment
    uv venv
    
    # Install jumpstarter packages
    uv pip install \
        ./python/packages/jumpstarter-cli \
        ./python/packages/jumpstarter-driver-composite \
        ./python/packages/jumpstarter-driver-power \
        ./python/packages/jumpstarter-driver-opendal
    
    log_info "✓ Jumpstarter installed"
}

# Step 5: Setup test environment
setup_test_environment() {
    log_info "Setting up test environment..."
    
    cd "$REPO_ROOT"
    
    # Get the controller endpoint
    export ENDPOINT=$(helm get values jumpstarter --output json | jq -r '."jumpstarter-controller".grpc.endpoint')
    log_info "Controller endpoint: $ENDPOINT"
    
    # Setup exporters directory
    echo "Setting up exporters directory in /etc/jumpstarter/exporters..., will need permissions"
    sudo mkdir -p /etc/jumpstarter/exporters
    sudo chown "$USER" /etc/jumpstarter/exporters
    
    # Create service accounts
    log_info "Creating service accounts..."
    kubectl create -n "${JS_NAMESPACE}" sa test-client-sa
    kubectl create -n "${JS_NAMESPACE}" sa test-exporter-sa
    
    # Create a marker file to indicate setup is complete
    echo "ENDPOINT=$ENDPOINT" > "$REPO_ROOT/.e2e-setup-complete"
    echo "JS_NAMESPACE=$JS_NAMESPACE" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REPO_ROOT=$REPO_ROOT" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "SCRIPT_DIR=$SCRIPT_DIR" >> "$REPO_ROOT/.e2e-setup-complete"
    
    # Set SSL certificate paths for Python to use the generated CA
    echo "SSL_CERT_FILE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REQUESTS_CA_BUNDLE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    
    log_info "✓ Test environment ready"
}

# Main execution
main() {
    log_info "=== Jumpstarter E2E Setup ==="
    log_info "Namespace: $JS_NAMESPACE"
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
