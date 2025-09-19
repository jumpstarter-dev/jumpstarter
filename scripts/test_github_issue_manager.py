#!/usr/bin/env python3
"""
Simple test script to verify the GitHub issue manager works correctly.

This test doesn't require a real GitHub token - it tests the argument parsing
and basic functionality without making API calls.
"""

import sys
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add the scripts directory to the path so we can import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

try:
    from github_issue_manager import GitHubIssueManager
except ImportError:
    print("Error: Could not import github_issue_manager module")
    sys.exit(1)


class TestGitHubIssueManager(unittest.TestCase):
    """Test cases for the GitHub Issue Manager."""
    
    @patch('github_issue_manager.requests.Session')
    def test_initialization_with_token(self, mock_session):
        """Test that the manager initializes correctly with a token."""
        manager = GitHubIssueManager(token="test_token")
        self.assertEqual(manager.token, "test_token")
        self.assertIsNotNone(manager.session)
    
    def test_initialization_without_token(self):
        """Test that the manager raises an error without a token."""
        # Clear any environment variables
        old_github_token = os.environ.get('GITHUB_TOKEN')
        old_gh_token = os.environ.get('GH_TOKEN')
        
        if 'GITHUB_TOKEN' in os.environ:
            del os.environ['GITHUB_TOKEN']
        if 'GH_TOKEN' in os.environ:
            del os.environ['GH_TOKEN']
        
        try:
            with self.assertRaises(ValueError):
                GitHubIssueManager()
        finally:
            # Restore environment variables
            if old_github_token:
                os.environ['GITHUB_TOKEN'] = old_github_token
            if old_gh_token:
                os.environ['GH_TOKEN'] = old_gh_token

    @patch('github_issue_manager.requests.Session')
    def test_get_issue(self, mock_session):
        """Test getting issue information."""
        # Mock the response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'number': 626,
            'title': 'Test Issue',
            'labels': [{'name': 'existing-label'}]
        }
        mock_session.return_value.get.return_value = mock_response
        
        manager = GitHubIssueManager(token="test_token")
        result = manager.get_issue("owner/repo", 626)
        
        self.assertEqual(result['number'], 626)
        self.assertEqual(result['title'], 'Test Issue')

    @patch('github_issue_manager.requests.Session')
    def test_add_label_new(self, mock_session):
        """Test adding a new label to an issue."""
        # Mock get issue response
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {
            'number': 626,
            'labels': [{'name': 'existing-label'}]
        }
        
        # Mock patch response
        mock_patch_response = MagicMock()
        
        mock_session_instance = mock_session.return_value
        mock_session_instance.get.return_value = mock_get_response
        mock_session_instance.patch.return_value = mock_patch_response
        
        manager = GitHubIssueManager(token="test_token")
        result = manager.add_label("owner/repo", 626, "bug")
        
        self.assertTrue(result)
        # Verify patch was called with correct labels
        mock_session_instance.patch.assert_called_once()
        call_args = mock_session_instance.patch.call_args
        self.assertIn('labels', call_args[1]['json'])
        labels = call_args[1]['json']['labels']
        self.assertIn('existing-label', labels)
        self.assertIn('bug', labels)

    @patch('github_issue_manager.requests.Session')
    def test_set_issue_type(self, mock_session):
        """Test setting issue type."""
        # Mock get issue response
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {
            'number': 626,
            'labels': [{'name': 'existing-label'}, {'name': 'type:old'}]
        }
        
        # Mock patch response
        mock_patch_response = MagicMock()
        
        mock_session_instance = mock_session.return_value
        mock_session_instance.get.return_value = mock_get_response
        mock_session_instance.patch.return_value = mock_patch_response
        
        manager = GitHubIssueManager(token="test_token")
        result = manager.set_issue_type("owner/repo", 626, "bug")
        
        self.assertTrue(result)
        # Verify patch was called with correct labels
        mock_session_instance.patch.assert_called_once()
        call_args = mock_session_instance.patch.call_args
        labels = call_args[1]['json']['labels']
        self.assertIn('existing-label', labels)
        self.assertIn('type:bug', labels)
        # Old type label should be removed
        self.assertNotIn('type:old', labels)


def test_script_syntax():
    """Test that the script has valid syntax."""
    script_path = os.path.join(os.path.dirname(__file__), 'github_issue_manager.py')
    
    try:
        with open(script_path, 'r') as f:
            compile(f.read(), script_path, 'exec')
        print("✓ Script syntax is valid")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax error in script: {e}")
        return False


def test_help_output():
    """Test that the script shows help output correctly."""
    import subprocess
    
    script_path = os.path.join(os.path.dirname(__file__), 'github_issue_manager.py')
    
    try:
        result = subprocess.run([
            sys.executable, script_path, '--help'
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and 'Manage GitHub issues' in result.stdout:
            print("✓ Help output is working correctly")
            return True
        else:
            print(f"✗ Help output failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"✗ Error testing help output: {e}")
        return False


if __name__ == '__main__':
    print("Testing GitHub Issue Manager...")
    print()
    
    # Test syntax
    syntax_ok = test_script_syntax()
    
    # Test help output
    help_ok = test_help_output()
    
    # Run unit tests
    print()
    print("Running unit tests...")
    unittest.main(verbosity=2, exit=False)
    
    print()
    if syntax_ok and help_ok:
        print("✓ All basic tests passed!")
    else:
        print("✗ Some tests failed!")
        sys.exit(1)