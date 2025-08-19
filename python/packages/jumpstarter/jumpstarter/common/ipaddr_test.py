from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter.common.ipaddr import get_minikube_ip


class TestIPAddressDetection:
    """Test IP address detection functions."""

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_get_minikube_ip_success(self, mock_subprocess):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"192.168.49.2\n", b"")
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await get_minikube_ip()

        assert result == "192.168.49.2"
        mock_subprocess.assert_called_once_with(
            "minikube",
            "ip",
            stdout=-1,
            stderr=-1,  # asyncio.subprocess.PIPE constants
        )

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_get_minikube_ip_with_profile(self, mock_subprocess):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"192.168.49.3\n", b"")
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await get_minikube_ip("test-profile")

        assert result == "192.168.49.3"
        mock_subprocess.assert_called_once_with("minikube", "ip", "-p", "test-profile", stdout=-1, stderr=-1)

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_get_minikube_ip_custom_binary(self, mock_subprocess):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"10.0.0.5\n", b"")
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await get_minikube_ip(minikube="custom-minikube")

        assert result == "10.0.0.5"
        mock_subprocess.assert_called_once_with("custom-minikube", "ip", stdout=-1, stderr=-1)

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_get_minikube_ip_failure(self, mock_subprocess):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"error: cluster not found\n")
        mock_process.returncode = 1
        mock_subprocess.return_value = mock_process

        with pytest.raises(RuntimeError, match="error: cluster not found"):
            await get_minikube_ip()

    @pytest.mark.asyncio
    @patch("asyncio.create_subprocess_exec")
    async def test_get_minikube_ip_profile_and_custom_binary(self, mock_subprocess):
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"172.16.0.1\n", b"")
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        result = await get_minikube_ip("my-profile", "my-minikube")

        assert result == "172.16.0.1"
        mock_subprocess.assert_called_once_with("my-minikube", "ip", "-p", "my-profile", stdout=-1, stderr=-1)
