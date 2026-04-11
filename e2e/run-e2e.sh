#!/usr/bin/env bash
# Jumpstarter End-to-End Test Runner
# This script runs the e2e test suite (assumes setup-e2e.sh was run first)
#
# The tests are implemented using Go + Ginkgo. Label filters can be used to
# run specific subsets:
#   --label-filter "core"            - run core tests only
#   --label-filter "hooks"           - run hooks tests only
#   --label-filter "direct-listener" - run direct-listener tests only
#   --label-filter "!operator-only"  - skip operator-specific tests

set -euo pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get the monorepo root (parent of e2e directory)
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load shared utilities
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Check if setup was completed
check_setup() {
    if ! load_setup_config "$REPO_ROOT"; then
        log_error "Setup not complete! Please run setup-e2e.sh first:"
        log_error "  bash e2e/setup-e2e.sh"
        log_error ""
        log_error "Or in CI mode, run the full setup automatically"
        return 1
    fi

    # Export SSL certificate paths for Python
    export SSL_CERT_FILE
    export REQUESTS_CA_BUNDLE
    export LOGIN_ENDPOINT

    if ! verify_namespace; then
        log_error "Please run setup-e2e.sh again."
        return 1
    fi

    log_info "Setup verified"
    return 0
}

# Run the tests
run_tests() {
    log_info "Running jumpstarter e2e tests..."

    cd "$REPO_ROOT"

    # Activate virtual environment
    activate_venv "python/.venv" || {
        log_error "Please run setup-e2e.sh first."
        exit 1
    }

    # Use insecure GRPC for testing
    export JUMPSTARTER_GRPC_INSECURE=1
    # Use native DNS resolver to avoid c-ares issues with nip.io on macOS
    export GRPC_DNS_RESOLVER=native

    log_info "Running ginkgo e2e tests..."
    run_ginkgo "$SCRIPT_DIR/test" "${GINKGO_LABEL_FILTER:-}"
}

# Full setup and run (for CI or first-time use)
full_run() {
    log_info "Running full setup + test cycle..."

    if [ -f "$SCRIPT_DIR/setup-e2e.sh" ]; then
        bash "$SCRIPT_DIR/setup-e2e.sh"
    else
        log_error "setup-e2e.sh not found!"
        exit 1
    fi

    # After setup, load the configuration
    if ! load_setup_config "$REPO_ROOT"; then
        log_warn "Could not load setup config after running setup-e2e.sh"
    fi
    # Export SSL certificate paths for Python
    export SSL_CERT_FILE="${SSL_CERT_FILE:-}"
    export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}"

    run_tests
}

# Main execution
main() {
    # Default namespace
    export E2E_TEST_NS="${E2E_TEST_NS:-jumpstarter-lab}"

    log_info "=== Jumpstarter E2E Test Runner ==="
    log_info "Namespace: $E2E_TEST_NS"
    log_info "Repository Root: $REPO_ROOT"
    echo ""

    # If --full flag is passed, always run full setup
    if [[ "${1:-}" == "--full" ]]; then
        full_run
    # In CI mode, check if setup was already done
    elif is_ci; then
        if check_setup 2>/dev/null; then
            log_info "Setup already complete, skipping setup and running tests..."
            run_tests
        else
            log_info "Setup not found in CI, running full setup..."
            full_run
        fi
    else
        # Local development: require setup to be done first
        if check_setup; then
            run_tests
        else
            log_error ""
            log_error "Setup is required before running tests."
            log_error ""
            log_error "Options:"
            log_error "  1. Run setup first: bash e2e/setup-e2e.sh"
            log_error "  2. Run full cycle:  bash e2e/run-e2e.sh --full"
            exit 1
        fi
    fi

    echo ""
    log_info "All e2e tests completed successfully!"
}

# Run main function
main "$@"
