#!/usr/bin/env bash
# Shared installation helpers for e2e setup scripts.
# Requires: log_info, log_error functions and REPO_ROOT variable.

# install_jumpstarter installs Python packages either from pre-built wheels
# (when PREBUILT_WHEELS_DIR is set) or via make sync.
install_jumpstarter() {
    log_info "Installing jumpstarter..."

    cd "$REPO_ROOT"
    if [ -n "${PREBUILT_WHEELS_DIR:-}" ]; then
        if [ ! -d "${PREBUILT_WHEELS_DIR}" ]; then
            log_error "PREBUILT_WHEELS_DIR is set but directory does not exist: ${PREBUILT_WHEELS_DIR}"
            exit 1
        fi
        local whl_count
        whl_count=$(find "${PREBUILT_WHEELS_DIR}" -maxdepth 1 -name '*.whl' | wc -l)
        if [ "$whl_count" -eq 0 ]; then
            log_error "PREBUILT_WHEELS_DIR contains no .whl files: ${PREBUILT_WHEELS_DIR}"
            exit 1
        fi
        log_info "Installing from pre-built wheels in ${PREBUILT_WHEELS_DIR} (${whl_count} wheels)..."
        cd python
        uv venv .venv --python 3.12
        uv pip install --python .venv/bin/python "${PREBUILT_WHEELS_DIR}"/*.whl
        cd ..
    else
        cd python
        make sync
        cd ..
    fi
    log_info "✓ Jumpstarter python installed"
}
