"""Tests for controller version resolution."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter_kubernetes.controller import get_latest_compatible_controller_version
from jumpstarter_kubernetes.exceptions import JumpstarterKubernetesError


class TestGetLatestCompatibleControllerVersion:
    """Test controller version resolution from Quay.io API."""

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_requests_correct_url(self, mock_session_class):
        tags_response = {"tags": [{"name": "v0.5.0"}]}
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        captured_url = None
        original_get = mock_session.get

        def capture_get(url, **kwargs):
            nonlocal captured_url
            captured_url = url
            return original_get(url, **kwargs)

        mock_session.get = capture_get

        await get_latest_compatible_controller_version("v0.5.0")

        assert captured_url == "https://quay.io/api/v1/repository/jumpstarter-dev/jumpstarter-operator/tag/"

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_returns_compatible_version(self, mock_session_class):
        tags_response = {
            "tags": [
                {"name": "v0.5.0"},
                {"name": "v0.5.1"},
                {"name": "v0.5.2"},
                {"name": "v0.6.0"},
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        result = await get_latest_compatible_controller_version("v0.5.0")

        assert result == "v0.5.2"

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_falls_back_to_latest_when_no_compatible(self, mock_session_class):
        tags_response = {
            "tags": [
                {"name": "v0.6.0"},
                {"name": "v0.7.0"},
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        result = await get_latest_compatible_controller_version("v0.5.0")

        assert result == "v0.7.0"

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_returns_latest_when_no_client_version(self, mock_session_class):
        tags_response = {
            "tags": [
                {"name": "v0.5.0"},
                {"name": "v0.6.0"},
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        result = await get_latest_compatible_controller_version(None)

        assert result == "v0.6.0"

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_skips_invalid_semver_tags(self, mock_session_class):
        tags_response = {
            "tags": [
                {"name": "latest"},
                {"name": "sha-abc123"},
                {"name": "v0.5.0"},
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        result = await get_latest_compatible_controller_version("v0.5.0")

        assert result == "v0.5.0"

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_raises_on_unexpected_response_format(self, mock_session_class):
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value="not a dict")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        with pytest.raises(JumpstarterKubernetesError, match="Unexpected response"):
            await get_latest_compatible_controller_version("v0.5.0")

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_raises_on_no_valid_versions(self, mock_session_class):
        tags_response = {"tags": [{"name": "latest"}]}
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        with pytest.raises(JumpstarterKubernetesError, match="No valid controller versions"):
            await get_latest_compatible_controller_version("v0.5.0")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_client_version(self):
        with pytest.raises(JumpstarterKubernetesError, match="Invalid client version"):
            await get_latest_compatible_controller_version("not-a-version")

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_raises_on_fetch_failure(self, mock_session_class):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        with pytest.raises(JumpstarterKubernetesError, match="Failed to fetch controller versions"):
            await get_latest_compatible_controller_version("v0.5.0")

    @pytest.mark.asyncio
    @patch("aiohttp.ClientSession")
    async def test_skips_malformed_tag_entries(self, mock_session_class):
        tags_response = {
            "tags": [
                {"no_name_key": "oops"},
                "not a dict",
                {"name": "v0.5.0"},
            ]
        }
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value=tags_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = lambda url, **kwargs: mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_class.return_value = mock_session

        result = await get_latest_compatible_controller_version("v0.5.0")

        assert result == "v0.5.0"
