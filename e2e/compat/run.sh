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

# Load shared utilities
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

# Check if setup was completed
check_setup() {
    if ! load_setup_config "$REPO_ROOT"; then
        log_error "Setup not complete! Please run e2e/compat/setup.sh first."
        return 1
    fi

    if ! verify_namespace; then
        log_error "Please run e2e/compat/setup.sh again."
        return 1
    fi

    log_info "Setup verified"
    return 0
}

# Run the tests
run_tests() {
    log_info "Running jumpstarter compatibility e2e tests..."

    cd "$REPO_ROOT"

    # Activate virtual environment
    activate_venv "python/.venv" || {
        log_error "Please run e2e/compat/setup.sh first."
        exit 1
    }

    # Use insecure GRPC for testing
    export JUMPSTARTER_GRPC_INSECURE=1

    COMPAT_TEST="${COMPAT_TEST:-old-controller}"
    log_info "Running compat test: $COMPAT_TEST"

    local label_filter=""
    case "$COMPAT_TEST" in
        old-controller)
            label_filter="old-controller"
            ;;
        old-client)
            label_filter="old-client"
            ;;
        *)
            log_error "Unknown COMPAT_TEST: $COMPAT_TEST (expected 'old-controller' or 'old-client')"
            exit 1
            ;;
    esac

    run_ginkgo "$SCRIPT_DIR/../test" "$label_filter"
}

# Main execution
main() {
    export E2E_TEST_NS="${E2E_TEST_NS:-${JS_NAMESPACE:-jumpstarter-lab}}"

    log_info "=== Jumpstarter Compatibility E2E Test Runner ==="
    log_info "Test: ${COMPAT_TEST:-old-controller}"
    log_info "Namespace: $E2E_TEST_NS"
    log_info "Repository Root: $REPO_ROOT"
    echo ""

    check_setup
    run_tests

    echo ""
    log_info "=== Compatibility e2e tests completed successfully! ==="
}

main "$@"
