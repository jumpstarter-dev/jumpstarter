#!/usr/bin/env bash
# Jumpstarter Compatibility E2E Test Runner
# This script runs the compatibility test suite (assumes setup.sh was run first)
#
# Environment variables:
#   COMPAT_TEST - Which test to run: "old-controller" or "old-client"

set -euo pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get the monorepo root
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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

# Check if setup was completed
check_setup() {
    if [ ! -f "$REPO_ROOT/.e2e-setup-complete" ]; then
        log_error "Setup not complete! Please run e2e/compat/setup.sh first."
        return 1
    fi

    # Load setup configuration
    source "$REPO_ROOT/.e2e-setup-complete"

    # Export OLD_JMP if set
    export OLD_JMP="${OLD_JMP:-}"

    # Verify critical components are still running
    if ! kubectl get namespace "$JS_NAMESPACE" &> /dev/null; then
        log_error "Namespace $JS_NAMESPACE not found. Please run e2e/compat/setup.sh again."
        return 1
    fi

    log_info "Setup verified"
    return 0
}

# Setup environment for bats
setup_bats_env() {
    local LOCAL_BATS_LIB="$REPO_ROOT/.bats/lib"

    if [ -d "$LOCAL_BATS_LIB" ]; then
        export BATS_LIB_PATH="$LOCAL_BATS_LIB:${BATS_LIB_PATH:-}"
        log_info "Set BATS_LIB_PATH to local libraries: $BATS_LIB_PATH"
    else
        log_warn "Local bats libraries not found at $LOCAL_BATS_LIB"
    fi
}

# Run the tests
run_tests() {
    log_info "Running jumpstarter compatibility e2e tests..."

    cd "$REPO_ROOT"

    # Activate virtual environment
    if [ -f python/.venv/bin/activate ]; then
        source python/.venv/bin/activate
    else
        log_error "Virtual environment not found. Please run e2e/compat/setup.sh first."
        exit 1
    fi

    # Use insecure GRPC for testing
    export JUMPSTARTER_GRPC_INSECURE=1

    # Export variables for bats
    export JS_NAMESPACE="${JS_NAMESPACE}"
    export ENDPOINT="${ENDPOINT}"

    # Setup bats environment
    setup_bats_env

    COMPAT_TEST="${COMPAT_TEST:-old-controller}"
    log_info "Running compat test: $COMPAT_TEST"

    case "$COMPAT_TEST" in
        old-controller)
            bats -x --show-output-of-passing-tests --verbose-run \
                "$SCRIPT_DIR/tests-old-controller.bats"
            ;;
        old-client)
            export OLD_JMP
            bats -x --show-output-of-passing-tests --verbose-run \
                "$SCRIPT_DIR/tests-old-client.bats"
            ;;
        *)
            log_error "Unknown COMPAT_TEST: $COMPAT_TEST (expected 'old-controller' or 'old-client')"
            exit 1
            ;;
    esac
}

# Main execution
main() {
    export JS_NAMESPACE="${JS_NAMESPACE:-jumpstarter-lab}"

    log_info "=== Jumpstarter Compatibility E2E Test Runner ==="
    log_info "Test: ${COMPAT_TEST:-old-controller}"
    log_info "Namespace: $JS_NAMESPACE"
    log_info "Repository Root: $REPO_ROOT"
    echo ""

    check_setup
    run_tests

    echo ""
    log_info "=== Compatibility e2e tests completed successfully! ==="
}

main "$@"
