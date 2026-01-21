#!/bin/bash
#
# import_pr.sh
#
# Imports a PR from an upstream Jumpstarter repository into the monorepo.
# This script fetches PR commits, generates patches, and applies them with
# the correct directory prefix for the monorepo structure.
#
# Usage: ./import_pr.sh <repo> <pr_number>
#
# Arguments:
#   repo       - One of: python, protocol, controller, e2e
#   pr_number  - The PR number from the upstream repository
#
# Example:
#   ./import_pr.sh python 123
#   ./import_pr.sh controller 45
#

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR="${SCRIPT_DIR}/.import-pr-temp"
PATCH_DIR="${TEMP_DIR}/patches"

# Repository mappings: repo_name -> "github_repo subdir"
declare -A REPO_MAP=(
    ["python"]="jumpstarter-dev/jumpstarter python"
    ["protocol"]="jumpstarter-dev/jumpstarter-protocol protocol"
    ["controller"]="jumpstarter-dev/jumpstarter-controller controller"
    ["e2e"]="jumpstarter-dev/jumpstarter-e2e e2e"
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Cleanup function
cleanup() {
    local exit_code=$?
    if [ -d "${TEMP_DIR}" ]; then
        log_info "Cleaning up temporary directory..."
        rm -rf "${TEMP_DIR}"
    fi
    if [ $exit_code -ne 0 ]; then
        log_warn "Script exited with errors. Any partial changes may need to be reverted."
    fi
}

trap cleanup EXIT

# Print usage
usage() {
    echo "Usage: $0 <repo> <pr_number>"
    echo ""
    echo "Import a PR from an upstream repository into the monorepo."
    echo ""
    echo "Arguments:"
    echo "  repo       - One of: python, protocol, controller, e2e"
    echo "  pr_number  - The PR number from the upstream repository"
    echo ""
    echo "Examples:"
    echo "  $0 python 123     # Import PR #123 from jumpstarter repo"
    echo "  $0 controller 45  # Import PR #45 from controller repo"
    echo ""
    echo "Repository mappings:"
    echo "  python     -> jumpstarter-dev/jumpstarter"
    echo "  protocol   -> jumpstarter-dev/jumpstarter-protocol"
    echo "  controller -> jumpstarter-dev/jumpstarter-controller"
    echo "  e2e        -> jumpstarter-dev/jumpstarter-e2e"
    exit 1
}

# Check dependencies
check_dependencies() {
    log_step "Checking dependencies..."

    if ! command -v git &> /dev/null; then
        log_error "git is not installed. Please install git first."
        exit 1
    fi

    if ! command -v gh &> /dev/null; then
        log_error "gh (GitHub CLI) is not installed."
        echo "Install it from: https://cli.github.com/"
        exit 1
    fi

    # Check if gh is authenticated
    if ! gh auth status &> /dev/null; then
        log_error "gh is not authenticated. Please run 'gh auth login' first."
        exit 1
    fi

    log_info "All dependencies found."
}

# Validate arguments
validate_args() {
    if [ $# -lt 2 ]; then
        log_error "Missing arguments."
        usage
    fi

    local repo="$1"
    local pr_number="$2"

    # Validate repo name
    if [ -z "${REPO_MAP[$repo]}" ]; then
        log_error "Invalid repository name: ${repo}"
        echo "Valid options are: python, protocol, controller, e2e"
        exit 1
    fi

    # Validate PR number is numeric
    if ! [[ "$pr_number" =~ ^[0-9]+$ ]]; then
        log_error "PR number must be a positive integer: ${pr_number}"
        exit 1
    fi
}

# Fetch PR information
fetch_pr_info() {
    local github_repo="$1"
    local pr_number="$2"

    log_step "Fetching PR #${pr_number} info from ${github_repo}..."

    # Get PR details as JSON
    local pr_json
    pr_json=$(gh pr view "${pr_number}" --repo "${github_repo}" --json title,baseRefName,headRefName,commits,state 2>&1) || {
        log_error "Failed to fetch PR #${pr_number} from ${github_repo}"
        echo "Make sure the PR exists and you have access to the repository."
        exit 1
    }

    # Extract fields
    PR_TITLE=$(echo "$pr_json" | jq -r '.title')
    PR_BASE_BRANCH=$(echo "$pr_json" | jq -r '.baseRefName')
    PR_HEAD_BRANCH=$(echo "$pr_json" | jq -r '.headRefName')
    PR_COMMIT_COUNT=$(echo "$pr_json" | jq '.commits | length')
    PR_STATE=$(echo "$pr_json" | jq -r '.state')

    log_info "PR Title: ${PR_TITLE}"
    log_info "Base Branch: ${PR_BASE_BRANCH}"
    log_info "Head Branch: ${PR_HEAD_BRANCH}"
    log_info "Commits: ${PR_COMMIT_COUNT}"
    log_info "State: ${PR_STATE}"
}

# Clone repository and checkout PR
clone_and_checkout_pr() {
    local github_repo="$1"
    local pr_number="$2"

    log_step "Cloning repository and checking out PR..."

    # Create temp directory
    mkdir -p "${TEMP_DIR}"
    mkdir -p "${PATCH_DIR}"

    local clone_dir="${TEMP_DIR}/repo"

    # Clone the repository
    log_info "Cloning ${github_repo}..."
    gh repo clone "${github_repo}" "${clone_dir}" -- --depth=1 --no-single-branch 2>/dev/null || {
        # If shallow clone fails, try full clone
        gh repo clone "${github_repo}" "${clone_dir}"
    }

    cd "${clone_dir}"

    # Checkout the PR
    log_info "Checking out PR #${pr_number}..."
    gh pr checkout "${pr_number}" --repo "${github_repo}"

    # Fetch the base branch to ensure we have it
    log_info "Fetching base branch (${PR_BASE_BRANCH})..."
    git fetch origin "${PR_BASE_BRANCH}"

    CLONE_DIR="${clone_dir}"
}

# Generate patches for PR commits
generate_patches() {
    log_step "Generating patches..."

    cd "${CLONE_DIR}"

    # Find the merge base between the PR branch and the base branch
    local merge_base
    merge_base=$(git merge-base "origin/${PR_BASE_BRANCH}" HEAD)

    log_info "Merge base: ${merge_base}"

    # Count commits to be patched
    local commit_count
    commit_count=$(git rev-list --count "${merge_base}..HEAD")
    log_info "Commits to import: ${commit_count}"

    if [ "$commit_count" -eq 0 ]; then
        log_error "No commits found between merge base and HEAD."
        exit 1
    fi

    # Generate patches
    git format-patch -o "${PATCH_DIR}" "${merge_base}..HEAD"

    # Count generated patches
    PATCH_COUNT=$(ls -1 "${PATCH_DIR}"/*.patch 2>/dev/null | wc -l | tr -d ' ')
    log_info "Generated ${PATCH_COUNT} patch file(s)."
}

# Apply patches to monorepo
apply_patches() {
    local subdir="$1"
    local repo_name="$2"
    local pr_number="$3"

    log_step "Applying patches to monorepo..."

    cd "${SCRIPT_DIR}"

    # Create branch name
    local branch_name="import/${repo_name}-pr-${pr_number}"

    # Check if we're in a git repository
    if ! git rev-parse --git-dir &> /dev/null; then
        log_error "Not in a git repository. Please run this script from the monorepo root."
        exit 1
    fi

    # Check for uncommitted changes
    if ! git diff --quiet || ! git diff --cached --quiet; then
        log_error "You have uncommitted changes. Please commit or stash them first."
        exit 1
    fi

    # Check if branch already exists
    if git show-ref --verify --quiet "refs/heads/${branch_name}"; then
        log_error "Branch '${branch_name}' already exists."
        echo "Delete it with: git branch -D ${branch_name}"
        exit 1
    fi

    # Create and checkout new branch
    log_info "Creating branch: ${branch_name}"
    git checkout -b "${branch_name}"

    # Apply patches with directory prefix
    log_info "Applying patches with directory prefix: ${subdir}/"

    local patch_files=("${PATCH_DIR}"/*.patch)
    local applied=0
    local failed=0

    for patch in "${patch_files[@]}"; do
        if [ -f "$patch" ]; then
            local patch_name
            patch_name=$(basename "$patch")
            if git am --directory="${subdir}" "$patch" 2>/dev/null; then
                log_info "Applied: ${patch_name}"
                ((applied++))
            else
                log_error "Failed to apply: ${patch_name}"
                ((failed++))
                # Abort the am session
                git am --abort 2>/dev/null || true
                break
            fi
        fi
    done

    if [ "$failed" -gt 0 ]; then
        log_error "Failed to apply ${failed} patch(es)."
        echo ""
        echo "The patch may have conflicts. You can try to resolve them manually:"
        echo "  1. git checkout main"
        echo "  2. git branch -D ${branch_name}"
        echo "  3. Manually apply the changes from the upstream PR"
        exit 1
    fi

    APPLIED_COUNT=$applied
}

# Print success message and next steps
print_success() {
    local repo_name="$1"
    local pr_number="$2"
    local github_repo="$3"
    local branch_name="import/${repo_name}-pr-${pr_number}"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN} PR Import Successful!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "Summary:"
    echo "  - Source: ${github_repo}#${pr_number}"
    echo "  - Title: ${PR_TITLE}"
    echo "  - Branch: ${branch_name}"
    echo "  - Commits applied: ${APPLIED_COUNT}"
    echo ""
    echo "Next steps:"
    echo "  1. Review the imported commits:"
    echo "     git log --oneline main..HEAD"
    echo ""
    echo "  2. Push the branch and create a PR on the monorepo:"
    echo "     git push -u origin ${branch_name}"
    echo "     gh pr create --title \"${PR_TITLE}\" --body \"Imported from ${github_repo}#${pr_number}\""
    echo ""
    echo "  3. Or if you need to make changes first:"
    echo "     # Make your changes"
    echo "     git add -A && git commit --amend"
    echo ""
}

# Main execution
main() {
    local repo_name="$1"
    local pr_number="$2"

    echo ""
    log_info "Starting PR import: ${repo_name} #${pr_number}"
    echo ""

    # Validate arguments
    validate_args "$@"

    # Check dependencies
    check_dependencies
    echo ""

    # Parse repo mapping
    local repo_info="${REPO_MAP[$repo_name]}"
    local github_repo subdir
    read -r github_repo subdir <<< "${repo_info}"

    log_info "GitHub Repo: ${github_repo}"
    log_info "Monorepo Subdir: ${subdir}/"
    echo ""

    # Fetch PR info
    fetch_pr_info "${github_repo}" "${pr_number}"
    echo ""

    # Clone and checkout PR
    clone_and_checkout_pr "${github_repo}" "${pr_number}"
    echo ""

    # Generate patches
    generate_patches
    echo ""

    # Apply patches to monorepo
    apply_patches "${subdir}" "${repo_name}" "${pr_number}"
    echo ""

    # Print success message
    print_success "${repo_name}" "${pr_number}" "${github_repo}"
}

main "$@"
