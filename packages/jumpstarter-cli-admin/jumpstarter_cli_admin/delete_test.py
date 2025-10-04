from unittest.mock import ANY, AsyncMock, Mock, patch

import click
from click.testing import CliRunner
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    V1Alpha1Exporter,
    V1Alpha1ExporterStatus,
)
from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from .delete import delete
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1, UserConfigV1Alpha1Config

# Generate a random client name
CLIENT_NAME = "test"
# Default config path
CLIENT_CONFIG_PATH = ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH / (CLIENT_NAME + ".yaml")

CLIENT_ENDPOINT = "grpc://example.com:443"
CLIENT_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

CLIENT_CONFIG = ClientConfigV1Alpha1(
    alias=CLIENT_NAME,
    metadata=ObjectMeta(namespace="default", name=CLIENT_NAME),
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)

USER_CONFIG_CURRENT = UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=CLIENT_CONFIG))
USER_CONFIG_NOT_CURRENT = UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None))


@patch.object(ClientConfigV1Alpha1, "delete")
@patch.object(ClientConfigV1Alpha1, "exists")
@patch.object(ClientsV1Alpha1Api, "delete_client")
@patch.object(UserConfigV1Alpha1, "load_or_create")
@patch.object(UserConfigV1Alpha1, "save")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
def test_delete_client(
    _mock_load_kube_config,
    mock_save_user_config: Mock,
    mock_load_or_create_user_config: Mock,
    mock_delete_client: AsyncMock,
    mock_config_exists: Mock,
    mock_config_delete: Mock,
):
    runner = CliRunner()

    # Delete client object and config does not exist
    mock_config_exists.return_value = False
    result = runner.invoke(delete, ["client", CLIENT_NAME])
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_load_or_create_user_config.reset_mock()
    mock_config_delete.reset_mock()

    # Delete client object and delete config prompt = n
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="n\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object, not current client config and delete config prompt = Y
    mock_config_exists.return_value = True
    mock_load_or_create_user_config.return_value = USER_CONFIG_NOT_CURRENT
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="Y\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_called_once()
    mock_config_delete.assert_called_once_with(CLIENT_NAME)
    mock_save_user_config.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object, current client config and delete config prompt = Y
    mock_config_exists.return_value = True
    mock_load_or_create_user_config.return_value = USER_CONFIG_CURRENT
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="Y\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_called_once()
    mock_config_delete.assert_called_once_with(CLIENT_NAME)
    # Ensure that the current client config was reset to NONE
    mock_save_user_config.assert_called_once_with(USER_CONFIG_NOT_CURRENT)

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object nointeractive
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME, "--nointeractive"])
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object output name
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME, "--nointeractive", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == f"client.jumpstarter.dev/{CLIENT_NAME}\n"
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()


EXPORTER_NAME = "test"
EXPORTER_ENDPOINT = "grpc://example.com:443"
EXPORTER_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
# Default config path
default_config_path = ExporterConfigV1Alpha1.BASE_PATH / (EXPORTER_NAME + ".yaml")
# Create a test exporter config
EXPORTER_OBJECT = V1Alpha1Exporter(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Exporter",
    metadata=V1ObjectMeta(namespace="default", name=EXPORTER_NAME, creation_timestamp="2024-01-01T21:00:00Z"),
    status=V1Alpha1ExporterStatus(
        endpoint=EXPORTER_ENDPOINT, credential=V1ObjectReference(name=f"{EXPORTER_NAME}-credential"), devices=[]
    ),
)
EXPORTER_CONFIG = ExporterConfigV1Alpha1(
    alias=EXPORTER_NAME,
    metadata=ObjectMeta(namespace="default", name=EXPORTER_NAME),
    endpoint=EXPORTER_ENDPOINT,
    token=EXPORTER_TOKEN,
)


@patch.object(ExporterConfigV1Alpha1, "delete")
@patch.object(ExporterConfigV1Alpha1, "exists")
@patch.object(ExportersV1Alpha1Api, "delete_exporter")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_delete_exporter(
    _mock_load_kube_config,
    mock_delete_exporter: AsyncMock,
    mock_config_exists: Mock,
    mock_config_delete: Mock,
):
    runner = CliRunner()

    # Delete exporter object and config does not exist
    mock_config_exists.return_value = False
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME])
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object and config exists, delete = n
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME], input="n\n")
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object and config exists, delete = Y
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME], input="Y\n")
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_called_with(EXPORTER_NAME)

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object nointeractive
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME, "--nointeractive"])
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object output name
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME, "--nointeractive", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == f"exporter.jumpstarter.dev/{EXPORTER_NAME}\n"
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()


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
