from unittest.mock import patch

import pytest

from .fls import download_fls, get_fls_binary, get_fls_github_url


@pytest.mark.parametrize(
    "arch,version,expected_binary",
    [
        ("aarch64", "0.1.9", "fls-aarch64-linux"),
        ("arm64", "0.1.9", "fls-aarch64-linux"),
        ("x86_64", "0.2.0", "fls-x86_64-linux"),
        ("amd64", "0.2.0", "fls-x86_64-linux"),
        ("unknown", "0.1.9", "fls-aarch64-linux"),  # defaults to aarch64
    ],
)
def test_get_fls_github_url_auto_detect(arch, version, expected_binary):
    """Test architecture auto-detection from platform.machine()"""
    with patch("platform.machine", return_value=arch):
        url = get_fls_github_url(version)
        assert url == f"https://github.com/jumpstarter-dev/fls/releases/download/{version}/{expected_binary}"


@pytest.mark.parametrize(
    "arch,version,expected_binary",
    [
        ("aarch64", "0.1.9", "fls-aarch64-linux"),
        ("AARCH64", "0.1.9", "fls-aarch64-linux"),  # case insensitive
        ("x86_64", "0.2.0", "fls-x86_64-linux"),
    ],
)
def test_get_fls_github_url_explicit_arch(arch, version, expected_binary):
    """Test explicit architecture parameter (used by flashers for target device)"""
    url = get_fls_github_url(version, arch=arch)
    assert url == f"https://github.com/jumpstarter-dev/fls/releases/download/{version}/{expected_binary}"


def test_get_fls_binary_with_custom_url():
    with patch("jumpstarter.common.fls.download_fls", return_value="/tmp/custom-fls") as mock_download:
        result = get_fls_binary(fls_binary_url="https://example.com/fls", allow_custom_binaries=True)

        mock_download.assert_called_once_with("https://example.com/fls")
        assert result == "/tmp/custom-fls"


def test_get_fls_binary_custom_url_security_check():
    """Test that custom URLs are blocked when allow_custom_binaries=False."""
    with pytest.raises(RuntimeError, match="Custom FLS binary URLs are disabled for security"):
        get_fls_binary(fls_binary_url="https://example.com/fls", allow_custom_binaries=False)


def test_get_fls_binary_with_version():
    with patch("jumpstarter.common.fls.download_fls", return_value="/tmp/fls-0.1.9") as mock_download:
        with patch("jumpstarter.common.fls.get_fls_github_url", return_value="https://github.com/...") as mock_url:
            result = get_fls_binary(fls_version="0.1.9")

            mock_url.assert_called_once_with("0.1.9")
            mock_download.assert_called_once()
            assert result == "/tmp/fls-0.1.9"


def test_get_fls_binary_falls_back_to_path():
    result = get_fls_binary()
    assert result == "fls"


def test_download_fls_success():
    from unittest.mock import MagicMock, mock_open

    mock_response = MagicMock()
    mock_response.read.side_effect = [b"binary data", b""]  # Simulate chunked read
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=None)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        with patch("tempfile.mkstemp", return_value=(99, "/tmp/fls-test")):
            with patch("os.close") as mock_close:
                with patch("pathlib.Path.chmod") as mock_chmod:
                    with patch("os.replace") as mock_replace:
                        with patch("builtins.open", mock_open()):
                            with patch("os.fsync"):
                                result = download_fls("https://example.com/fls")

                                mock_close.assert_called_once_with(99)
                                mock_urlopen.assert_called_once_with("https://example.com/fls", timeout=30.0)
                                mock_chmod.assert_called_once_with(0o755)
                                mock_replace.assert_called_once_with("/tmp/fls-test.part", "/tmp/fls-test")
                                assert result == "/tmp/fls-test"


def test_download_fls_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        with patch("tempfile.mkstemp", return_value=(99, "/tmp/fls-test")):
            with patch("os.close"):
                with patch("pathlib.Path.unlink"):
                    with pytest.raises(RuntimeError, match="Failed to download FLS"):
                        download_fls("https://example.com/fls")
