#!/bin/bash
#
# Wrapper script for the GitHub Issue Manager
#
# This script provides a convenient way to manage GitHub issues for the jumpstarter repository.
#

set -e

REPO="jumpstarter-dev/jumpstarter"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/github_issue_manager.py"

show_usage() {
    cat << 'EOF'
Usage: ./manage_issue.sh [OPTIONS] ISSUE_NUMBER

Manage GitHub issues for the jumpstarter repository.

OPTIONS:
    --add-label LABEL     Add a label to the issue
    --set-type TYPE       Set issue type (creates type:TYPE label)
    --list-labels         List current labels on the issue
    --help               Show this help message

EXAMPLES:
    # Add bug label to issue 626
    ./manage_issue.sh --add-label bug 626
    
    # Set issue type to bug for issue 626
    ./manage_issue.sh --set-type bug 626
    
    # List labels on issue 626
    ./manage_issue.sh --list-labels 626
    
    # Add bug label and set type to bug for issue 626
    ./manage_issue.sh --add-label bug --set-type bug 626

AUTHENTICATION:
    Set GITHUB_TOKEN or GH_TOKEN environment variable with your GitHub API token.

EOF
}

# Check if help is requested
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    show_usage
    exit 0
fi

# Parse arguments
ARGS=()
ADD_LABEL=""
SET_TYPE=""
LIST_LABELS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --add-label)
            ADD_LABEL="$2"
            shift 2
            ;;
        --set-type)
            SET_TYPE="$2"
            shift 2
            ;;
        --list-labels)
            LIST_LABELS=true
            shift
            ;;
        *)
            # Assume it's the issue number
            ISSUE_NUMBER="$1"
            shift
            ;;
    esac
done

# Check if issue number is provided
if [[ -z "$ISSUE_NUMBER" ]]; then
    echo "Error: Issue number is required"
    echo
    show_usage
    exit 1
fi

# Check if Python script exists
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Error: Python script not found at $PYTHON_SCRIPT"
    exit 1
fi

# Build command arguments
CMD_ARGS=(--repo "$REPO" --issue "$ISSUE_NUMBER")

if [[ -n "$ADD_LABEL" ]]; then
    CMD_ARGS+=(--add-label "$ADD_LABEL")
fi

if [[ -n "$SET_TYPE" ]]; then
    CMD_ARGS+=(--set-type "$SET_TYPE")
fi

if [[ "$LIST_LABELS" == true ]]; then
    CMD_ARGS+=(--list-labels)
fi

# Execute the Python script
echo "Managing issue #$ISSUE_NUMBER for repository $REPO..."
python3 "$PYTHON_SCRIPT" "${CMD_ARGS[@]}"