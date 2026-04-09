#!/usr/bin/env bash
# Common utilities shared between e2e runner scripts.
# Source this file from run-e2e.sh and compat/run.sh.

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

# activate_venv activates the Python virtual environment.
activate_venv() {
    local venv_path="${1:-python/.venv}"
    if [ -f "$venv_path/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$venv_path/bin/activate"
    else
        log_error "Virtual environment not found at $venv_path."
        return 1
    fi
}

# load_setup_config loads the .e2e-setup-complete configuration file and
# exports common variables needed by the Go test suite.
load_setup_config() {
    local repo_root="$1"

    if [ ! -f "$repo_root/.e2e-setup-complete" ]; then
        return 1
    fi

    # shellcheck disable=SC1091
    source "$repo_root/.e2e-setup-complete"

    export E2E_TEST_NS="${E2E_TEST_NS:-jumpstarter-lab}"
    export ENDPOINT="${ENDPOINT:-}"
    export REPO_ROOT="${repo_root}"
}

# verify_namespace checks that the test namespace exists in the cluster.
verify_namespace() {
    local ns="${1:-$E2E_TEST_NS}"
    if ! kubectl get namespace "$ns" &> /dev/null; then
        log_error "Namespace $ns not found."
        return 1
    fi
}

# run_ginkgo runs the ginkgo test suite with the given label filter and
# optional extra flags.
run_ginkgo() {
    local test_dir="$1"
    shift
    local label_filter="${1:-}"
    shift || true

    local flags=(-v --show-node-events --trace --timeout 30m)
    if [ -n "$label_filter" ]; then
        flags+=(--label-filter "$label_filter")
    fi

    cd "$test_dir"
    go run github.com/onsi/ginkgo/v2/ginkgo \
        "${flags[@]}" \
        "$@" \
        ./...
}
