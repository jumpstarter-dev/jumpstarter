from unittest.mock import ANY, patch

import click
from click.testing import CliRunner

from .delete import delete

# `delete client` / `delete exporter` run on the Rust core (forwarded via FFI) and are covered by
# the Rust admin tests + the e2e suite. Only the native `delete cluster` subcommand is tested here.


class TestClusterDeletion:
    """Test cluster deletion commands."""

    def setup_method(self):
        self.runner = CliRunner()

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_kind_with_confirmation(self, mock_delete):
        """Test deleting a Kind cluster with user confirmation"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_minikube_with_confirmation(self, mock_delete):
        """Test deleting a Minikube cluster with user confirmation"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--minikube", "minikube"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "minikube", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_auto_detect(self, mock_delete):
        """Test auto-detection of cluster type when neither --kind nor --minikube is specified"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", None, False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_with_force(self, mock_delete):
        """Test force deletion without confirmation prompt"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind", "--force"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "kind", True, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_default_name(self, mock_delete):
        """Test default cluster name is 'jumpstarter-lab'"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "--kind", "kind"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("jumpstarter-lab", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_confirmation_cancelled(self, mock_delete):
        """Test when user cancels deletion confirmation"""
        # Mock the delete function to raise Abort (user cancelled)
        mock_delete.side_effect = click.Abort()

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code != 0
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_force_skips_confirmation(self, mock_delete):
        """Test that --force flag skips confirmation"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--minikube", "minikube", "--force"])

        assert result.exit_code == 0
        # Verify force=True was passed
        mock_delete.assert_called_once_with("test-cluster", "minikube", True, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_not_found(self, mock_delete):
        """Test error when cluster doesn't exist"""
        mock_delete.side_effect = click.ClickException('No cluster named "test-cluster" found')

        result = self.runner.invoke(delete, ["cluster", "test-cluster"])

        assert result.exit_code != 0
        assert 'No cluster named "test-cluster" found' in result.output
        mock_delete.assert_called_once_with("test-cluster", None, False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_kind_not_installed(self, mock_delete):
        """Test error when kind is not installed"""
        mock_delete.side_effect = click.ClickException("Kind is not installed")

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code != 0
        assert "Kind is not installed" in result.output
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_minikube_not_installed(self, mock_delete):
        """Test error when minikube is not installed"""
        mock_delete.side_effect = click.ClickException("Minikube is not installed")

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--minikube", "minikube"])

        assert result.exit_code != 0
        assert "Minikube is not installed" in result.output
        mock_delete.assert_called_once_with("test-cluster", "minikube", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_does_not_exist(self, mock_delete):
        """Test error when specified cluster doesn't exist"""
        mock_delete.side_effect = click.ClickException('Kind cluster "test-cluster" does not exist')

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code != 0
        assert 'Kind cluster "test-cluster" does not exist' in result.output
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_name_output(self, mock_delete):
        """Test --output=name only prints the cluster name"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind", "--output", "name"])

        assert result.exit_code == 0
        assert result.output.strip() == "test-cluster"
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_name_output_still_prompts_for_confirmation(self, mock_delete):
        """Test --output=name without --force still prompts for confirmation (uses SilentWithConfirmCallback)"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind", "--output", "name"])

        assert result.exit_code == 0
        # Verify that force=False was passed, which means confirmation should be prompted
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)
        # Verify the callback type is SilentWithConfirmCallback by checking its behavior
        callback_arg = mock_delete.call_args[0][3]
        from jumpstarter_cli_common.callbacks import SilentWithConfirmCallback

        assert isinstance(callback_arg, SilentWithConfirmCallback)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_name_output_with_force_uses_force_callback(self, mock_delete):
        """Test --output=name with --force uses ForceClickCallback"""
        mock_delete.return_value = None

        result = self.runner.invoke(
            delete, ["cluster", "test-cluster", "--kind", "kind", "--output", "name", "--force"]
        )

        assert result.exit_code == 0
        # Verify that force=True was passed
        mock_delete.assert_called_once_with("test-cluster", "kind", True, ANY)
        # Verify the callback type is ForceClickCallback
        callback_arg = mock_delete.call_args[0][3]
        from jumpstarter_cli_common.callbacks import ForceClickCallback

        assert isinstance(callback_arg, ForceClickCallback)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_normal_output(self, mock_delete):
        """Test normal output messages (mocked through delete_cluster_by_name)"""
        mock_delete.return_value = None

        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)
        # Note: Output messages are handled by delete_cluster_by_name function itself

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_both_types_specified_behavior(self, mock_delete):
        """Test that specifying both --kind and --minikube, kind takes precedence"""
        mock_delete.return_value = None

        # In the delete command, kind is checked first, so it takes precedence
        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "kind", "--minikube", "minikube"])

        assert result.exit_code == 0
        # Should use kind since it's checked first in the if/elif chain
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

    @patch("jumpstarter_cli_admin.delete.delete_cluster_by_name")
    def test_delete_cluster_with_custom_binaries(self, mock_delete):
        """Test that custom binary names are handled correctly"""
        mock_delete.return_value = None

        # Test with custom kind binary name
        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--kind", "my-kind"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "kind", False, ANY)

        mock_delete.reset_mock()

        # Test with custom minikube binary name
        result = self.runner.invoke(delete, ["cluster", "test-cluster", "--minikube", "my-minikube"])

        assert result.exit_code == 0
        mock_delete.assert_called_once_with("test-cluster", "minikube", False, ANY)
