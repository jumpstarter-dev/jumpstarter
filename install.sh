#!/bin/bash
set -e

# Colors for output
RED="$(tput setaf 1)"
GREEN="$(tput setaf 2)"
YELLOW="$(tput setaf 3)"
BLUE="$(tput setaf 4)"
NC="$(tput sgr0)" # No Color

# Default values
INSTALL_DIR="${HOME}/.local/jumpstarter"
VENV_DIR="${INSTALL_DIR}/venv"
SET_SCRIPT="${INSTALL_DIR}/set"
DEFAULT_SOURCE="release-0.6"

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1" >&2
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1" >&2
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1" >&2
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Function to show usage
show_usage() {
    cat << EOF
Jumpstarter Installer

Usage: $0 [OPTIONS]

OPTIONS:
    -s, --source SOURCE    Installation source (default: release-0.6)
                          Available sources:
                          - release-0.6: Stable release 0.6 (recommended)
                          - latest: Latest stable release (when available)
                          - rc: Latest release candidate (when available)
                          - main: Latest development version
    -d, --dir DIR         Installation directory (default: ~/.local/jumpstarter)
    -h, --help            Show this help message

EXAMPLES:
    $0                    # Install stable release 0.6 (recommended)
    $0 -s release-0.6    # Install stable release 0.6
    $0 -s main           # Install latest development version
    $0 -s rc             # Install latest release candidate (when available)
    $0 -d /opt/jumpstarter  # Install to custom directory

After installation, activate the environment, or add this to your profile with:
    source "${INSTALL_DIR}/set"
EOF
}

# Function to get the appropriate index URL based on source
get_index_url() {
    local source="$1"
    case "${source}" in
        "latest")
            echo "https://pkg.jumpstarter.dev/simple"
            ;;
        "rc")
            echo "https://pkg.jumpstarter.dev/rc/simple"
            ;;
        "main")
            echo "https://pkg.jumpstarter.dev/main/simple"
            ;;
        release-*)
            echo "https://pkg.jumpstarter.dev/${source}/simple"
            ;;
        *)
                    print_error "Invalid source: ${source}"
        print_error "Available sources: latest, rc, main, release-0.6"
            exit 1
            ;;
    esac
}

# Function to get the latest version of jumpstarter-all from an index
get_latest_version() {
    local index_url="$1"

    print_info "Checking for jumpstarter-all versions using pip index versions"

    # Use pip index versions to get the latest version
    local output
    if ! output="$(python3 -m pip index versions --index-url "${index_url}" --pre jumpstarter-all 2>/dev/null)"; then
        print_error "Failed to get jumpstarter-all versions from ${index_url}"
        return 1
    fi

    # Extract the version from the output
    # Expected format: "jumpstarter-all (0.7.0.dev65+g637d0eb)"
    local latest_version=$(echo "${output}" | grep "^jumpstarter-all (" | sed 's/jumpstarter-all (\([^)]*\)).*/\1/')

    if [ -z "${latest_version}" ]; then
        print_error "Could not parse version from pip index output"
        print_error "Output: ${output}"
        return 1
    fi

    print_success "Found jumpstarter-all version: ${latest_version}"
    echo "${latest_version}"
}

# Function to check if Python 3.11+ is available
check_python() {
    if command -v python3 >/dev/null 2>&1; then
        local version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        local major=$(echo ${version} | cut -d. -f1)
        local minor=$(echo ${version} | cut -d. -f2)

        if [ "${major}" -eq 3 ] && [ "${minor}" -ge 11 ]; then
            print_success "Found Python ${version}"
            return 0
        else
            print_error "Python 3.11+ required, found ${version}"
            return 1
        fi
    else
        print_error "Python 3 not found"
        return 1
    fi
}

# Function to check if pip is available
check_pip() {
    if python3 -m pip --version >/dev/null 2>&1; then
        print_success "Found pip3"
        return 0
    else
        print_error "pip3 not found"
        return 1
    fi
}

# Function to check if curl is available
check_curl() {
    if command -v curl >/dev/null 2>&1; then
        print_success "Found curl"
        return 0
    else
        print_error "curl not found (required for version detection)"
        return 1
    fi
}

# Function to create virtual environment
create_venv() {
    print_info "Creating virtual environment in ${VENV_DIR}"

    if [ -d "${VENV_DIR}" ]; then
        print_warning "Virtual environment already exists, removing it"
        rm -rf "${VENV_DIR}"
    fi

    python3 -m venv "${VENV_DIR}"
    print_success "Virtual environment created"
}

# Function to install jumpstarter-all
install_jumpstarter() {
    local source="$1"
    local index_url=$(get_index_url "${source}")

    print_info "Installing jumpstarter-all from ${source}"
    print_info "Using index URL: ${index_url}"

    # Get the latest version from the index
    local version=$(get_latest_version "${index_url}")
    if [ $? -ne 0 ]; then
        print_error "Could not determine version for jumpstarter-all from ${source}"
        exit 1
    fi

    print_info "Installing jumpstarter-all==${version}"

    # Activate virtual environment and install
    source "${VENV_DIR}/bin/activate"

    # We don't upgrade pip here, because it might break the installation
    # Install jumpstarter-all with specific version and index URL
    print_info "Installing jumpstarter-all==${version}..."
    if ! python3 -m pip install --extra-index-url "${index_url}" "jumpstarter-all==${version}"; then
        print_error "Failed to install jumpstarter-all==${version}"
        print_error "This might be due to network issues or the package not being available"
        exit 1
    fi

    print_success "jumpstarter-all==${version} installed successfully"
}

# Function to create set script
create_set_script() {
    print_info "Creating set script at ${SET_SCRIPT}"

    cat > "${SET_SCRIPT}" << EOF
#!/bin/bash
# Jumpstarter environment activation script

if [ ! -d "${INSTALL_DIR}/venv" ]; then
    echo "Error: Jumpstarter virtual environment not found at ${INSTALL_DIR}/venv"
    echo "Please run the installer again"
fi

# Add jumpstarter bin to PATH if not already there
if [[ ":\$PATH:" != *":${INSTALL_DIR}/bin:"* ]]; then
    export PATH="${INSTALL_DIR}/bin:\$PATH"
fi

EOF

    chmod +x "${SET_SCRIPT}"
    print_success "Set script created"
}

# Function to create bin directory and symlinks
create_bin_symlinks() {
    local bin_dir="${INSTALL_DIR}/bin"

    print_info "Creating bin directory and symlinks"
    mkdir -p "${bin_dir}"


    # Find and symlink other jumpstarter-related commands
    for cmd in "${VENV_DIR}/bin"/j*; do
        if [ -f "${cmd}" ]; then
            local cmd_name=$(basename "${cmd}")
            ln -sf "${cmd}" "${bin_dir}/${cmd_name}"
        fi
    done

    print_success "Bin symlinks created"
}

# Function to show post-installation instructions
show_post_install() {
    cat >&2 << EOF

Installation completed successfully!

To activate the Jumpstarter environment, run:
    source ${INSTALL_DIR}/set

Or add this line to your shell profile (~/.bashrc, ~/.zshrc, etc.):
    source ${INSTALL_DIR}/set

After activation, you can use jumpstarter commands:
    jmp --help

Installation details:
- Virtual environment: ${INSTALL_DIR}/venv
- Setup script: ${INSTALL_DIR}/set
- Bin directory: ${INSTALL_DIR}/bin

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--source)
            SOURCE="$2"
            shift 2
            ;;
        -d|--dir)
            INSTALL_DIR="$2"
            VENV_DIR="${INSTALL_DIR}/venv"
            SET_SCRIPT="${INSTALL_DIR}/set"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Set default source if not specified
SOURCE="${SOURCE:-${DEFAULT_SOURCE}}"

# Main installation process
main() {
    print_info "Jumpstarter Installer"
    print_info "Source: ${SOURCE}"
    print_info "Installation directory: ${INSTALL_DIR}"
    echo >&2

    # Check prerequisites
    print_info "Checking prerequisites..."
    check_python || exit 1
    check_pip || exit 1
    check_curl || exit 1
    echo >&2

    # Create installation directory
    print_info "Creating installation directory"
    mkdir -p "${INSTALL_DIR}"
    print_success "Installation directory created: ${INSTALL_DIR}"
    echo >&2

    # Create virtual environment
    create_venv
    echo >&2

    # Install jumpstarter-all
    install_jumpstarter "${SOURCE}"
    echo >&2

    # Create set script
    create_set_script
    echo >&2

    # Create bin symlinks
    create_bin_symlinks
    echo >&2

    # Show post-installation instructions
    show_post_install
}

# Run main function
main "$@"
