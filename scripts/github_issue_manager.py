#!/usr/bin/env python3
"""
GitHub Issue Management Script

This script provides functionality to manage GitHub issues, including:
- Adding labels to issues
- Setting issue types (when supported)
- Listing issue information

Usage:
    python github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --add-label bug
    python github_issue_manager.py --repo jumpstarter-dev/jumpstarter --issue 626 --set-type bug
"""

import argparse
import os
import sys
from typing import List, Optional

try:
    import requests
except ImportError:
    print("Error: requests library not found. Please install with: pip install requests")
    sys.exit(1)


class GitHubIssueManager:
    def __init__(self, token: Optional[str] = None):
        """Initialize the GitHub Issue Manager.
        
        Args:
            token: GitHub API token. If not provided, will try to get from environment.
        """
        self.token = token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if not self.token:
            raise ValueError(
                "GitHub token is required. Set GITHUB_TOKEN or GH_TOKEN environment variable, "
                "or pass token directly."
            )
        
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'jumpstarter-github-issue-manager'
        })
        self.base_url = 'https://api.github.com'

    def get_issue(self, repo: str, issue_number: int) -> dict:
        """Get issue information.
        
        Args:
            repo: Repository name in format 'owner/repo'
            issue_number: Issue number
            
        Returns:
            Issue data as dictionary
        """
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def add_label(self, repo: str, issue_number: int, label: str) -> bool:
        """Add a label to an issue.
        
        Args:
            repo: Repository name in format 'owner/repo'
            issue_number: Issue number
            label: Label to add
            
        Returns:
            True if successful
        """
        # First get current labels
        issue = self.get_issue(repo, issue_number)
        current_labels = [l['name'] for l in issue.get('labels', [])]
        
        # Check if label already exists
        if label in current_labels:
            print(f"Label '{label}' already exists on issue #{issue_number}")
            return True
        
        # Add the new label
        new_labels = current_labels + [label]
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        
        data = {'labels': new_labels}
        response = self.session.patch(url, json=data)
        response.raise_for_status()
        
        print(f"Successfully added label '{label}' to issue #{issue_number}")
        return True

    def set_issue_type(self, repo: str, issue_number: int, issue_type: str) -> bool:
        """Set issue type using labels (since GitHub doesn't have native issue types).
        
        This method will:
        1. Remove any existing type labels (type:*)
        2. Add the new type label (type:{issue_type})
        
        Args:
            repo: Repository name in format 'owner/repo'
            issue_number: Issue number
            issue_type: Type to set (e.g., 'bug', 'feature', 'enhancement')
            
        Returns:
            True if successful
        """
        # Get current labels
        issue = self.get_issue(repo, issue_number)
        current_labels = [l['name'] for l in issue.get('labels', [])]
        
        # Remove existing type labels
        new_labels = [label for label in current_labels if not label.startswith('type:')]
        
        # Add the new type label
        type_label = f"type:{issue_type}"
        if type_label not in new_labels:
            new_labels.append(type_label)
        
        # Update the issue
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        data = {'labels': new_labels}
        response = self.session.patch(url, json=data)
        response.raise_for_status()
        
        print(f"Successfully set issue type to '{issue_type}' for issue #{issue_number}")
        return True

    def list_labels(self, repo: str, issue_number: int) -> List[str]:
        """List all labels on an issue.
        
        Args:
            repo: Repository name in format 'owner/repo'
            issue_number: Issue number
            
        Returns:
            List of label names
        """
        issue = self.get_issue(repo, issue_number)
        return [l['name'] for l in issue.get('labels', [])]


def main():
    parser = argparse.ArgumentParser(description='Manage GitHub issues')
    parser.add_argument('--repo', required=True, help='Repository in format owner/repo')
    parser.add_argument('--issue', type=int, required=True, help='Issue number')
    parser.add_argument('--add-label', help='Add a label to the issue')
    parser.add_argument('--set-type', help='Set issue type (creates type:X label)')
    parser.add_argument('--list-labels', action='store_true', help='List current labels')
    parser.add_argument('--token', help='GitHub API token (or use GITHUB_TOKEN env var)')

    args = parser.parse_args()

    try:
        manager = GitHubIssueManager(token=args.token)
        
        if args.list_labels:
            labels = manager.list_labels(args.repo, args.issue)
            print(f"Labels on issue #{args.issue}:")
            for label in labels:
                print(f"  - {label}")
        
        if args.add_label:
            manager.add_label(args.repo, args.issue, args.add_label)
        
        if args.set_type:
            manager.set_issue_type(args.repo, args.issue, args.set_type)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()