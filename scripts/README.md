# Scripts Directory

This directory contains utility scripts for managing the Jumpstarter repository.

## github_issue_manager.py

A Python script for managing GitHub issues, including adding labels and setting issue types.

## manage_issue.sh

A convenient shell wrapper for the GitHub issue manager that simplifies common operations.

### Quick Usage

```bash
# Add bug label to issue 626
./scripts/manage_issue.sh --add-label bug 626

# Set issue type to bug for issue 626  
./scripts/manage_issue.sh --set-type bug 626

# Add bug label AND set type to bug for issue 626
./scripts/manage_issue.sh --add-label bug --set-type bug 626

# List current labels on issue 626
./scripts/manage_issue.sh --list-labels 626
```

### Detailed Usage

#### Prerequisites
- Python 3.6+
- `requests` library (`pip install requests`)
- GitHub API token set in `GITHUB_TOKEN` or `GH_TOKEN` environment variable

#### Examples

```bash
# Using Python script directly
python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --list-labels

# Add a bug label to an issue
python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug

# Set issue type to bug
python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --set-type bug

# Combine operations
python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug --set-type bug
```

#### Authentication

The scripts require a GitHub API token. You can provide it in several ways:

1. Set the `GITHUB_TOKEN` environment variable
2. Set the `GH_TOKEN` environment variable
3. Pass it directly with the `--token` argument (Python script only)

```bash
# Using environment variable
export GITHUB_TOKEN=your_token_here
./scripts/manage_issue.sh --add-label bug 626

# Using command line argument (Python script only)
python scripts/github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug --token your_token_here
```