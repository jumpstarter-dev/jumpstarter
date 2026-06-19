from unittest.mock import patch

import click
from click.testing import CliRunner

from .create import create

# `create client` / `create exporter` run on the Rust core (forwarded via FFI) and are covered by
# the Rust admin tests + the e2e suite. Only the native `create cluster` subcommand is tested here.


class TestClusterCreation:
    """Test cluster creation commands."""

    def setup_method(self):
        self.runner = CliRunner()

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_kind_minimal(self, mock_validate, mock_create):
        """Test creating a Kind cluster with minimal options"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code == 0
        assert "Creating kind cluster" in result.output
        mock_validate.assert_called_once_with("kind", None, None)
        mock_create.assert_called_once()

        # Verify the arguments passed to create_cluster_and_install
        args, kwargs = mock_create.call_args
        assert args[0] == "kind"  # cluster_type
        assert args[1] is False  # force_recreate_cluster
        assert args[2] == "test-cluster"  # cluster_name
        assert args[3] == ""  # kind_extra_args
        assert args[4] == ""  # minikube_extra_args
        assert args[5] == "kind"  # kind binary
        assert args[6] == "minikube"  # minikube binary

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_minikube_minimal(self, mock_validate, mock_create):
        """Test creating a Minikube cluster with minimal options"""
        mock_validate.return_value = "minikube"
        mock_create.return_value = None

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--minikube", "minikube"])

        assert result.exit_code == 0
        assert "Creating minikube cluster" in result.output
        mock_validate.assert_called_once_with(None, "minikube", None)
        mock_create.assert_called_once()

        # Verify the arguments passed to create_cluster_and_install
        args, kwargs = mock_create.call_args
        assert args[0] == "minikube"  # cluster_type
        assert args[1] is False  # force_recreate_cluster
        assert args[2] == "test-cluster"  # cluster_name

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_auto_detect(self, mock_validate, mock_create):
        """Test auto-detection of cluster type when neither --kind nor --minikube is specified"""
        mock_validate.return_value = "kind"  # Auto-detected as kind
        mock_create.return_value = None

        result = self.runner.invoke(create, ["cluster", "test-cluster"])

        assert result.exit_code == 0
        assert "Auto-detected kind as the cluster type" in result.output
        mock_validate.assert_called_once_with(None, None, None)
        mock_create.assert_called_once()

    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_both_types_error(self, mock_validate):
        """Test that specifying both --kind and --minikube raises an error"""
        mock_validate.side_effect = click.ClickException("You can only select one local cluster type")

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind", "--minikube", "minikube"])

        assert result.exit_code != 0
        assert "You can only select one local cluster type" in result.output
        mock_validate.assert_called_once_with("kind", "minikube", None)

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_with_jumpstarter_installation(self, mock_validate, mock_create):
        """Test creating cluster and installing Jumpstarter (default behavior)"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code == 0
        assert "and installing Jumpstarter" in result.output
        assert "created and Jumpstarter installed successfully" in result.output
        mock_create.assert_called_once()

        # Verify install_jumpstarter is True by default
        kwargs = mock_create.call_args[1]
        assert kwargs.get("install_jumpstarter", True) is True

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_skip_install(self, mock_validate, mock_create):
        """Test creating cluster with --skip-install flag"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind", "--skip-install"])

        assert result.exit_code == 0
        assert "Creating kind cluster" in result.output
        assert "is ready for Jumpstarter installation" in result.output
        mock_create.assert_called_once()

        # Verify install_jumpstarter is False
        kwargs = mock_create.call_args[1]
        assert kwargs.get("install_jumpstarter", True) is False

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_with_custom_endpoints(self, mock_validate, mock_create):
        """Test with custom IP, basedomain, and endpoints"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        result = self.runner.invoke(create, [
            "cluster", "test-cluster", "--kind", "kind",
            "--ip", "192.168.1.100",
            "--basedomain", "custom.example.com",
            "--grpc-endpoint", "grpc.custom.example.com:9000",
            "--router-endpoint", "router.custom.example.com:9001"
        ])

        assert result.exit_code == 0
        mock_create.assert_called_once()

        # Verify custom endpoint options
        kwargs = mock_create.call_args[1]
        assert kwargs.get("ip") == "192.168.1.100"
        assert kwargs.get("basedomain") == "custom.example.com"
        assert kwargs.get("grpc_endpoint") == "grpc.custom.example.com:9000"
        assert kwargs.get("router_endpoint") == "router.custom.example.com:9001"

    @patch("jumpstarter_cli_admin.create.click.confirm")
    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_force_recreate_confirmed(self, mock_validate, mock_create, mock_confirm):
        """Test force recreate with user confirmation"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None
        mock_confirm.return_value = True

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind", "--force-recreate"])

        assert result.exit_code == 0
        mock_create.assert_called_once()

        # Verify force_recreate_cluster is True
        args = mock_create.call_args[0]
        assert args[1] is True  # force_recreate_cluster

    @patch("jumpstarter_cli_admin.create.click.confirm")
    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_force_recreate_cancelled(self, mock_validate, mock_create, mock_confirm):
        """Test force recreate when user cancels"""
        mock_validate.return_value = "kind"
        mock_create.side_effect = click.Abort()
        mock_confirm.return_value = False

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind", "--force-recreate"])

        assert result.exit_code != 0
        # Note: create_cluster_and_install itself handles the confirmation

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_with_extra_args(self, mock_validate, mock_create):
        """Test with extra Kind/Minikube arguments"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        result = self.runner.invoke(create, [
            "cluster", "test-cluster", "--kind", "kind",
            "--kind-extra-args", "--verbosity=1 --retain",
            "--minikube-extra-args", "--memory=4096"
        ])

        assert result.exit_code == 0
        mock_create.assert_called_once()

        # Verify extra args
        args = mock_create.call_args[0]
        assert args[3] == "--verbosity=1 --retain"  # kind_extra_args
        assert args[4] == "--memory=4096"  # minikube_extra_args

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_with_extra_certs(self, mock_validate, mock_create):
        """Test with custom CA certificates"""
        mock_validate.return_value = "kind"
        mock_create.return_value = None

        # Create a temporary cert file for the test
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.crt', delete=False) as f:
            f.write("dummy cert content")
            cert_path = f.name

        try:
            result = self.runner.invoke(create, [
                "cluster", "test-cluster", "--kind", "kind",
                "--extra-certs", cert_path
            ])

            assert result.exit_code == 0
            mock_create.assert_called_once()

            # Verify extra_certs parameter (it's positional arg 7, index 7)
            # Note: Click resolves the path, so we need to check the resolved version
            args = mock_create.call_args[0]
            import os
            assert args[7] == os.path.realpath(cert_path)  # extra_certs
        finally:
            # Clean up
            import os
            os.unlink(cert_path)

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_kind_not_installed(self, mock_validate, mock_create):
        """Test error when kind is not installed"""
        mock_validate.return_value = "kind"
        mock_create.side_effect = click.ClickException("kind is not installed")

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--kind", "kind"])

        assert result.exit_code != 0
        assert "kind is not installed" in result.output

    @patch("jumpstarter_cli_admin.create.create_cluster_and_install")
    @patch("jumpstarter_cli_admin.create.validate_cluster_type_selection")
    def test_create_cluster_minikube_not_installed(self, mock_validate, mock_create):
        """Test error when minikube is not installed"""
        mock_validate.return_value = "minikube"
        mock_create.side_effect = click.ClickException("minikube is not installed")

        result = self.runner.invoke(create, ["cluster", "test-cluster", "--minikube", "minikube"])

        assert result.exit_code != 0
        assert "minikube is not installed" in result.output
