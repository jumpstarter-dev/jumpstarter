#!/bin/bash
#
# Demonstration script showing how to use the GitHub issue management tools
# to add the 'bug' label and set the type to 'bug' for issue 626
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== GitHub Issue Management Tools Demonstration ==="
echo ""

echo "This demonstration shows how to use the new GitHub issue management tools"
echo "to add the 'bug' label and set the type to 'bug' for issue 626."
echo ""

echo "Step 1: Check current labels on issue 626"
echo "Command: ./scripts/manage_issue.sh --list-labels 626"
echo ""
echo "Note: This requires GITHUB_TOKEN to be set in your environment."
echo "Example: export GITHUB_TOKEN=your_token_here"
echo ""

echo "Step 2: Add 'bug' label to issue 626"
echo "Command: ./scripts/manage_issue.sh --add-label bug 626"
echo ""

echo "Step 3: Set issue type to 'bug' for issue 626"  
echo "Command: ./scripts/manage_issue.sh --set-type bug 626"
echo ""

echo "Alternative: Do both operations in one command"
echo "Command: ./scripts/manage_issue.sh --add-label bug --set-type bug 626"
echo ""

echo "=== Alternative using Python script directly ==="
echo ""
echo "You can also use the Python script directly for more control:"
echo ""
echo "# List current labels"
echo "python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --list-labels"
echo ""
echo "# Add bug label"
echo "python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug"
echo ""
echo "# Set type to bug"
echo "python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --set-type bug"
echo ""
echo "# Do both operations at once"
echo "python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug --set-type bug"
echo ""

echo "=== Features ==="
echo ""
echo "- Add any label to GitHub issues"
echo "- Set issue types using type:* labels"
echo "- List existing labels on issues"
echo "- Combine multiple operations in one command"
echo "- Works with any GitHub repository"
echo "- Secure authentication via environment variables"
echo ""

echo "=== Authentication ==="
echo ""
echo "To use these tools, you need a GitHub API token with repo access."
echo "Set it as an environment variable:"
echo ""
echo "export GITHUB_TOKEN=your_token_here"
echo "# or"
echo "export GH_TOKEN=your_token_here"
echo ""

echo "=== Files Created ==="
echo ""
echo "- scripts/github_issue_manager.py - Main Python script for GitHub issue management"
echo "- scripts/manage_issue.sh - Convenient shell wrapper for jumpstarter repository"
echo "- scripts/README.md - Documentation for the scripts"
echo "- scripts/demo.sh - This demonstration script"
echo ""

echo "For more information, see scripts/README.md"