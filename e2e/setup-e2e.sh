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
    
    log_info "✓ Dependencies installed"
}

# Step 2: Install e2e tools (cfssl, cfssljson, yq) as prebuilt binaries
E2E_TOOLS_BIN="$REPO_ROOT/.e2e/bin"
CFSSL_VERSION="1.6.5"
YQ_VERSION="v4.52.5"

# SHA256 checksums for prebuilt binaries (from upstream release assets)
get_expected_sha256() {
    case "$1" in
        cfssl_linux_amd64)      echo "ff4d3a1387ea3e1ee74f4bb8e5ffe9cbab5bee43c710333c206d14199543ebdf" ;;
        cfssl_linux_arm64)      echo "bc1a0b3a33ab415f3532af1d52cad7c9feec0156df2069f1cbbb64255485f108" ;;
        cfssl_darwin_amd64)     echo "6625b252053d9499bf26102b8fa78d7f675de56703d0808f8ff6dcf43121fa0c" ;;
        cfssl_darwin_arm64)     echo "9a38b997ac23bc2eed89d6ad79ea5ae27c29710f66fdabdff2aa16eaaadc30d4" ;;
        cfssljson_linux_amd64)  echo "09fbcb7a3b3d6394936ea61eabff1e8a59a8ac3b528deeb14cf66cdbbe9a534f" ;;
        cfssljson_linux_arm64)  echo "a389793bc2376116fe2fff996b4a2f772a59a4f65048a5cfb4789b2c0ea4a7c9" ;;
        cfssljson_darwin_amd64) echo "1529a7a163801be8cf7d7a347b0346cc56cc8f351dbc0131373b6fb76bb4ab64" ;;
        cfssljson_darwin_arm64) echo "no-prebuilt-binary-available" ;;
        yq_linux_amd64)         echo "75d893a0d5940d1019cb7cdc60001d9e876623852c31cfc6267047bc31149fa9" ;;
        yq_linux_arm64)         echo "90fa510c50ee8ca75544dbfffed10c88ed59b36834df35916520cddc623d9aaa" ;;
        yq_darwin_amd64)        echo "6e399d1eb466860c3202d231727197fdce055888c5c7bec6964156983dd1559d" ;;
        yq_darwin_arm64)        echo "45a12e64d4bd8a31c72ee1b889e81f1b1110e801baad3d6f030c111db0068de0" ;;
        *) echo "" ;;
    esac
}

verify_sha256() {
    local file="$1" expected="$2"
    local actual
    actual=$(shasum -a 256 "$file" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        log_error "SHA256 mismatch for $file"
        log_error "  expected: $expected"
        log_error "  actual:   $actual"
        rm -f "$file"
        return 1
    fi
}

try_download_verified() {
    local dest="$1" url="$2" hash_key="$3"
    local expected_hash
    expected_hash=$(get_expected_sha256 "$hash_key")
    if [ -z "$expected_hash" ]; then
        return 1
    fi
    if curl -fsSL -o "$dest" "$url" && verify_sha256 "$dest" "$expected_hash"; then
        chmod +x "$dest"
        return 0
    fi
    rm -f "$dest"
    return 1
}

download_or_go_install() {
    local name="$1" url="$2" go_pkg="$3" hash_key="$4"
    local dest="$E2E_TOOLS_BIN/$name"
    if [ -x "$dest" ]; then
        return 0
    fi
    log_info "Downloading ${name}..."
    if try_download_verified "$dest" "$url" "$hash_key"; then
        return 0
    fi
    # On darwin/arm64, try the amd64 binary via Rosetta before compiling
    if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        local amd64_url="${url/darwin_arm64/darwin_amd64}"
        local amd64_key="${hash_key/darwin_arm64/darwin_amd64}"
        if [ "$amd64_url" != "$url" ]; then
            log_info "Trying amd64 binary via Rosetta for ${name}..."
            if try_download_verified "$dest" "$amd64_url" "$amd64_key"; then
                return 0
            fi
        fi
    fi
    log_warn "No verified prebuilt binary available, falling back to go install for ${name}..."
    rm -f "$dest"
    GOBIN="$E2E_TOOLS_BIN" go install "$go_pkg"
}

install_e2e_tools() {
    log_info "Installing e2e tools..."
    mkdir -p "$E2E_TOOLS_BIN"

    local arch
    case "$(uname -m)" in
        x86_64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) log_error "Unsupported architecture: $(uname -m)"; exit 1 ;;
    esac

    local os
    case "$(uname -s)" in
        Linux)  os="linux" ;;
        Darwin) os="darwin" ;;
        *) log_error "Unsupported OS: $(uname -s)"; exit 1 ;;
    esac

    download_or_go_install cfssl \
        "https://github.com/cloudflare/cfssl/releases/download/v${CFSSL_VERSION}/cfssl_${CFSSL_VERSION}_${os}_${arch}" \
        "github.com/cloudflare/cfssl/cmd/cfssl@v${CFSSL_VERSION}" \
        "cfssl_${os}_${arch}"

    download_or_go_install cfssljson \
        "https://github.com/cloudflare/cfssl/releases/download/v${CFSSL_VERSION}/cfssljson_${CFSSL_VERSION}_${os}_${arch}" \
        "github.com/cloudflare/cfssl/cmd/cfssljson@v${CFSSL_VERSION}" \
        "cfssljson_${os}_${arch}"

    download_or_go_install yq \
        "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_${os}_${arch}" \
        "github.com/mikefarah/yq/v4@${YQ_VERSION}" \
        "yq_${os}_${arch}"

    export PATH="$E2E_TOOLS_BIN:$PATH"
    log_info "✓ e2e tools installed to $E2E_TOOLS_BIN"
}

# Step 3: Deploy dex
deploy_dex() {
    log_info "Deploying dex..."
    
    cd "$REPO_ROOT"
    
    # Generate certificates using prebuilt cfssl binaries
    log_info "Generating certificates..."
    cfssl gencert -initca "$SCRIPT_DIR"/ca-csr.json | cfssljson -bare ca -
    cfssl gencert -ca=ca.pem -ca-key=ca-key.pem \
        -config="$SCRIPT_DIR"/ca-config.json -profile=www "$SCRIPT_DIR"/dex-csr.json | \
        cfssljson -bare server
    

    make -C controller cluster
    
    # Create dex namespace and TLS secret (idempotent)
    log_info "Creating dex namespace and secrets..."
    kubectl create namespace dex --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n dex create secret tls dex-tls \
        --cert=server.pem \
        --key=server-key.pem \
        --dry-run=client -o yaml | kubectl apply -f -
    
    # Create .e2e directory for configuration files
    log_info "Creating .e2e directory for local configuration..."
    mkdir -p "$REPO_ROOT/.e2e"
    
    # Copy values.kind.yaml to .e2e and inject the CA certificate
    log_info "Creating values file with CA certificate..."
    cp "$SCRIPT_DIR"/values.kind.yaml "$REPO_ROOT/.e2e/values.kind.yaml"
    
    log_info "Injecting CA certificate into values..."
    yq -i \
        '.jumpstarter-controller.config.authentication.jwt[0].issuer.certificateAuthority = load_str("ca.pem")' \
        "$REPO_ROOT/.e2e/values.kind.yaml"
    
    log_info "✓ Values file with CA certificate created at .e2e/values.kind.yaml"
    
    # Create OIDC reviewer binding (idempotent)
    log_info "Creating OIDC reviewer cluster role binding..."
    kubectl create clusterrolebinding oidc-reviewer \
        --clusterrole=system:service-account-issuer-discovery \
        --group=system:unauthenticated \
        --dry-run=client -o yaml | kubectl apply -f -
    
    # Install dex via helm (upgrade --install is idempotent)
    log_info "Installing dex via helm..."
    helm repo add dex https://charts.dexidp.io
    helm upgrade --install --namespace dex --wait -f "$SCRIPT_DIR"/dex.values.yaml dex dex/dex
    
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

# Step 4: Deploy jumpstarter controller
deploy_controller() {
    log_info "Deploying jumpstarter controller..."
    
    cd "$REPO_ROOT"
    
    log_info "Deploying controller with CA certificate using operator..."
    OPERATOR_USE_DEX=true DEX_CA_FILE="$REPO_ROOT/ca.pem" make -C controller deploy
    
    log_info "✓ Controller deployed"
}

# Step 5: Install jumpstarter (shared helper)
# shellcheck source=lib/install.sh
source "$SCRIPT_DIR/lib/install.sh"

# Step 6: Setup test environment
setup_test_environment() {
    log_info "Setting up test environment..."
    
    cd "$REPO_ROOT"
    
    # Get the controller endpoint from the Jumpstarter CR
    # Note: We declare BASEDOMAIN separately from assignment so that command
    # failures propagate under set -e (local VAR=$(...) masks exit codes).
    local BASEDOMAIN
    BASEDOMAIN=$(kubectl get jumpstarter -n "${JS_NAMESPACE}" jumpstarter -o jsonpath='{.spec.baseDomain}')
    if [ -z "${BASEDOMAIN}" ]; then
        log_error "Failed to get baseDomain from Jumpstarter CR in namespace ${JS_NAMESPACE}"
        exit 1
    fi
    export ENDPOINT="grpc.${BASEDOMAIN}:8082"
    export LOGIN_ENDPOINT="login.${BASEDOMAIN}:8086"
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
    
    # Create service accounts (idempotent)
    log_info "Creating service accounts..."
    kubectl create -n "${JS_NAMESPACE}" sa test-client-sa --dry-run=client -o yaml | kubectl apply -f -
    kubectl create -n "${JS_NAMESPACE}" sa test-exporter-sa --dry-run=client -o yaml | kubectl apply -f -
    
    # Create a marker file to indicate setup is complete
    echo "ENDPOINT=$ENDPOINT" > "$REPO_ROOT/.e2e-setup-complete"
    echo "LOGIN_ENDPOINT=$LOGIN_ENDPOINT" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "E2E_TEST_NS=$JS_NAMESPACE" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REPO_ROOT=$REPO_ROOT" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "SCRIPT_DIR=$SCRIPT_DIR" >> "$REPO_ROOT/.e2e-setup-complete"
    # Set SSL certificate paths for Python to use the generated CA
    echo "SSL_CERT_FILE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    echo "REQUESTS_CA_BUNDLE=$REPO_ROOT/ca.pem" >> "$REPO_ROOT/.e2e-setup-complete"
    
    # Export e2e tools bin so downstream scripts (tests) can find yq, cfssl, etc.
    echo "export PATH=\"$E2E_TOOLS_BIN:\$PATH\"" >> "$REPO_ROOT/.e2e-setup-complete"
    
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
    
    install_e2e_tools
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
