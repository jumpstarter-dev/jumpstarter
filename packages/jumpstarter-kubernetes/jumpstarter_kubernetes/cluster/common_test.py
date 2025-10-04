"""Tests for common cluster utilities and types."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter_kubernetes.cluster.common import (
    ClusterType,
    format_cluster_name,
    get_extra_certs_path,
    run_command,
    run_command_with_output,
    validate_cluster_name,
    validate_cluster_type,
)


class TestClusterType:
    """Test ClusterType type definition."""

    def test_cluster_type_kind(self):
        cluster_type: ClusterType = "kind"
        assert cluster_type == "kind"

    def test_cluster_type_minikube(self):
        cluster_type: ClusterType = "minikube"
        assert cluster_type == "minikube"


class TestValidateClusterType:
    """Test cluster type validation."""

    def test_validate_cluster_type_kind_only(self):
        result = validate_cluster_type("kind", None)
        assert result == "kind"

    def test_validate_cluster_type_minikube_only(self):
        result = validate_cluster_type(None, "minikube")
        assert result == "minikube"

    def test_validate_cluster_type_neither(self):
        result = validate_cluster_type(None, None)
        assert result is None

    def test_validate_cluster_type_both_raises_error(self):
        from jumpstarter_kubernetes.exceptions import ClusterTypeValidationError

        with pytest.raises(
            ClusterTypeValidationError, match='You can only select one local cluster type "kind" or "minikube"'
        ):
            validate_cluster_type("kind", "minikube")

    def test_validate_cluster_type_empty_strings(self):
        # Empty strings are not None, so first non-None value is returned
        result = validate_cluster_type("", "")
        assert result == "kind"  # First parameter is returned since "" is not None

    def test_validate_cluster_type_kind_with_empty_minikube(self):
        result = validate_cluster_type("kind", "")
        assert result == "kind"

    def test_validate_cluster_type_minikube_with_empty_kind(self):
        result = validate_cluster_type("", "minikube")
        assert result == "kind"  # Empty string is not None, so kind is returned


class TestGetExtraCertsPath:
    """Test extra certificates path handling."""

    def test_get_extra_certs_path_none(self):
        result = get_extra_certs_path(None)
        assert result is None

    def test_get_extra_certs_path_relative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cert_file = "test.crt"
            temp_cert_path = os.path.join(temp_dir, cert_file)

            # Create a temporary cert file
            with open(temp_cert_path, "w") as f:
                f.write("test cert")

            # Change to temp directory to test relative path
            original_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                result = get_extra_certs_path(cert_file)
                expected = os.path.abspath(cert_file)
                assert result == expected
                assert os.path.isabs(result)
            finally:
                os.chdir(original_cwd)

    def test_get_extra_certs_path_absolute(self):
        with tempfile.NamedTemporaryFile(suffix=".crt") as temp_file:
            result = get_extra_certs_path(temp_file.name)
            assert result == temp_file.name
            assert os.path.isabs(result)

    def test_get_extra_certs_path_nonexistent(self):
        # Function should still return absolute path even if file doesn't exist
        nonexistent_path = "/nonexistent/path/test.crt"
        result = get_extra_certs_path(nonexistent_path)
        assert result == nonexistent_path
        assert os.path.isabs(result)

    @patch("os.path.abspath")
    def test_get_extra_certs_path_calls_abspath(self, mock_abspath):
        mock_abspath.return_value = "/absolute/path/test.crt"
        result = get_extra_certs_path("test.crt")
        mock_abspath.assert_called_once_with("test.crt")
        assert result == "/absolute/path/test.crt"


class TestFormatClusterName:
    """Test cluster name formatting."""

    def test_format_cluster_name_normal(self):
        result = format_cluster_name("test-cluster")
        assert result == "test-cluster"

    def test_format_cluster_name_with_whitespace(self):
        result = format_cluster_name("  test-cluster  ")
        assert result == "test-cluster"

    def test_format_cluster_name_with_tabs(self):
        result = format_cluster_name("\ttest-cluster\t")
        assert result == "test-cluster"

    def test_format_cluster_name_with_newlines(self):
        result = format_cluster_name("\ntest-cluster\n")
        assert result == "test-cluster"

    def test_format_cluster_name_empty(self):
        result = format_cluster_name("")
        assert result == ""

    def test_format_cluster_name_only_whitespace(self):
        result = format_cluster_name("   ")
        assert result == ""


class TestValidateClusterName:
    """Test cluster name validation."""

    def test_validate_cluster_name_valid(self):
        result = validate_cluster_name("test-cluster")
        assert result == "test-cluster"

    def test_validate_cluster_name_with_whitespace(self):
        result = validate_cluster_name("  test-cluster  ")
        assert result == "test-cluster"

    def test_validate_cluster_name_empty_raises_error(self):
        from jumpstarter_kubernetes.exceptions import ClusterNameValidationError

        with pytest.raises(ClusterNameValidationError, match="Cluster name cannot be empty"):
            validate_cluster_name("")

    def test_validate_cluster_name_only_whitespace_raises_error(self):
        from jumpstarter_kubernetes.exceptions import ClusterNameValidationError

        with pytest.raises(ClusterNameValidationError, match="Cluster name cannot be empty"):
            validate_cluster_name("   ")

    def test_validate_cluster_name_none_raises_error(self):
        from jumpstarter_kubernetes.exceptions import ClusterNameValidationError

        # This would be caught by type checking, but test runtime behavior
        with pytest.raises(ClusterNameValidationError, match="Cluster name cannot be empty"):
            validate_cluster_name(None)

    def test_validate_cluster_name_with_special_chars(self):
        result = validate_cluster_name("test-cluster_123")
        assert result == "test-cluster_123"

    def test_validate_cluster_name_numeric(self):
        result = validate_cluster_name("123")
        assert result == "123"


class TestRunCommand:
    """Test run_command function."""

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"output\n", b"")
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            returncode, stdout, stderr = await run_command(["echo", "test"])

            assert returncode == 0
            assert stdout == "output"
            assert stderr == ""
            mock_subprocess.assert_called_once_with(
                "echo", "test", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

    @pytest.mark.asyncio
    async def test_run_command_failure(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"error message\n")
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            returncode, stdout, stderr = await run_command(["false"])

            assert returncode == 1
            assert stdout == ""
            assert stderr == "error message"

    @pytest.mark.asyncio
    async def test_run_command_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("command not found")):
            with pytest.raises(RuntimeError, match="Command not found: nonexistent"):
                await run_command(["nonexistent"])

    @pytest.mark.asyncio
    async def test_run_command_with_output_success(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.wait.return_value = 0
            mock_subprocess.return_value = mock_process

            returncode = await run_command_with_output(["echo", "test"])

            assert returncode == 0
            mock_subprocess.assert_called_once_with("echo", "test")

    @pytest.mark.asyncio
    async def test_run_command_with_output_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("command not found")):
            with pytest.raises(RuntimeError, match="Command not found: nonexistent"):
                await run_command_with_output(["nonexistent"])

    @pytest.mark.asyncio
    async def test_run_command_with_output_failure(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.wait.return_value = 1
            mock_subprocess.return_value = mock_process

            returncode = await run_command_with_output(["false"])

            assert returncode == 1
