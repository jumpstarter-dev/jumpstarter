import shlex
from concurrent.futures import CancelledError
from pathlib import PosixPath

import click
import pytest

from .client import BaseFlasherClient, FlashNonRetryableError, FlashRetryableError
from jumpstarter.common.exceptions import ArgumentError


class MockFlasherClient(BaseFlasherClient):
    """Mock client for testing without full initialization"""

    def __init__(self):
        self._manifest = None
        self._console_debug = False
        self._redaction_values = set()
        self.logger = type(
            "MockLogger",
            (),
            {
                "warning": lambda *args, **kwargs: None,
                "info": lambda *args, **kwargs: None,
                "error": lambda *args, **kwargs: None,
                "exception": lambda *args, **kwargs: None,
            },
        )()

    def close(self):
        pass


def test_validate_bearer_token_fails_invalid():
    """Test bearer token validation fails with invalid tokens"""
    client = MockFlasherClient()

    with pytest.raises(click.ClickException, match="Bearer token cannot be empty"):
        client._validate_bearer_token("")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token("token with spaces")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token('token"with"quotes')


def test_validate_oci_credentials_fails_when_partial():
    """Test OCI credential validation fails when only one value is provided"""
    client = MockFlasherClient()

    with pytest.raises(click.ClickException, match="OCI authentication requires both"):
        client._validate_oci_credentials("myuser", None)

    with pytest.raises(click.ClickException, match="OCI authentication requires both"):
        client._validate_oci_credentials(None, "mypassword")


def test_validate_oci_credentials_accepts_pair_and_strips_whitespace():
    """Test OCI credential validation accepts full username/password pair"""
    client = MockFlasherClient()

    username, password = client._validate_oci_credentials(" myuser ", " mypassword ")
    assert username == "myuser"
    assert password == "mypassword"


def test_resolve_oci_credentials_reads_env_for_oci_path(monkeypatch):
    """Test OCI credentials are read from environment for OCI paths."""
    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.setenv("OCI_PASSWORD", "env-pass")

    username, password = client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, None)
    assert username == "env-user"
    assert password == "env-pass"


def test_resolve_oci_credentials_ignores_env_for_non_oci_path(monkeypatch):
    """Test OCI credential env vars are ignored for non-OCI image paths."""
    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.setenv("OCI_PASSWORD", "env-pass")

    username, password = client._resolve_oci_credentials("https://example.com/image.raw.xz", None, None)
    assert username is None
    assert password is None


def test_resolve_oci_credentials_rejects_partial_env_for_oci_path(monkeypatch):
    """Test partial OCI env credentials are rejected for OCI paths."""
    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.delenv("OCI_PASSWORD", raising=False)

    with pytest.raises(click.ClickException, match="OCI authentication requires both"):
        client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, None)


def test_fls_oci_auth_env_sources_credentials_file():
    """Test OCI auth shell snippet sources the on-target credentials file"""
    client = MockFlasherClient()

    env_args = client._fls_oci_auth_env("oci://quay.io/org/image:tag", "/tmp/fls_creds")
    assert "set -o allexport;" in env_args
    assert "set +o allexport;" in env_args
    parsed = shlex.split(env_args)
    assert "." in parsed
    assert "/tmp/fls_creds;" in parsed


def test_fls_oci_auth_env_empty_for_non_oci_paths():
    """Test OCI auth env assignment is empty for non-OCI paths"""
    client = MockFlasherClient()

    env_args = client._fls_oci_auth_env("https://example.com/image.raw.xz", "/tmp/fls_creds")
    assert env_args == ""

    env_args = client._fls_oci_auth_env("oci://quay.io/org/image:tag", None)
    assert env_args == ""

    # PosixPath (converted by operator_for_path) must not crash
    env_args = client._fls_oci_auth_env(PosixPath("/images/image.raw.xz"), "/tmp/fls_creds")
    assert env_args == ""


def test_redact_sensitive_values_masks_username_and_password():
    """Test that sensitive values are redacted from output."""
    client = MockFlasherClient()
    client._redaction_values.update({"myuser", "mypassword"})

    result = client._redact_sensitive_values("user=myuser pass=mypassword")
    assert result == "user=*** pass=***"


def test_setup_fls_oci_credential_file():
    """Test secure credentials file setup commands."""
    client = MockFlasherClient()

    class MockConsole:
        def __init__(self):
            self.logfile_read = object()
            self.sent_lines = []
            self.expect_calls = []

        def sendline(self, line):
            self.sent_lines.append(line)

        def expect(self, prompt, timeout=None):
            self.expect_calls.append((prompt, timeout))

    console = MockConsole()
    creds_path = client._setup_fls_oci_credential_file(console, "#", "myuser", "my'password")
    assert creds_path == "/tmp/fls_creds"

    # Verify chunked base64 approach: creates file, writes b64 chunks, decodes, cleans up
    assert "true > /tmp/fls_creds" in console.sent_lines[0]
    assert "true > /tmp/fls_creds.b64" in console.sent_lines[1]

    # Find the base64 chunk lines (printf commands)
    b64_lines = [line for line in console.sent_lines if "printf" in line and ".b64" in line]
    assert len(b64_lines) >= 1

    # Verify decode step
    assert any("base64 -d" in line for line in console.sent_lines)
    assert any("chmod 600 /tmp/fls_creds" in line for line in console.sent_lines)

    # Verify the decoded content is correct
    import base64

    b64_data = ""
    for line in b64_lines:
        # Extract the base64 chunk from: printf '%s' <chunk> >> /tmp/fls_creds.b64
        parts = shlex.split(line)
        b64_data += parts[2]  # the chunk argument
    decoded = base64.b64decode(b64_data).decode()
    assert "FLS_REGISTRY_USERNAME=myuser" in decoded
    assert "FLS_REGISTRY_PASSWORD='my'\"'\"'password'" in decoded

    assert console.logfile_read is not None


def test_setup_fls_oci_credential_file_chunks_long_tokens():
    """Test that long JWT tokens are split into multiple base64 chunks."""
    client = MockFlasherClient()

    class MockConsole:
        def __init__(self):
            self.logfile_read = object()
            self.sent_lines = []
            self.expect_calls = []

        def sendline(self, line):
            self.sent_lines.append(line)

        def expect(self, prompt, timeout=None):
            self.expect_calls.append((prompt, timeout))

    console = MockConsole()
    # Simulate a 1400-char JWT token (similar to real Kubernetes service account tokens)
    long_token = "eyJ" + "a" * 1397

    creds_path = client._setup_fls_oci_credential_file(console, "#", "serviceaccount", long_token)
    assert creds_path == "/tmp/fls_creds"

    # With a 1400+ char token, the base64 encoding should produce multiple chunks
    b64_lines = [line for line in console.sent_lines if "printf" in line and ".b64" in line]
    assert len(b64_lines) > 1, f"Expected multiple chunks for long token, got {len(b64_lines)}"

    # Each printf line should be well under serial buffer limits
    for line in b64_lines:
        assert len(line) < 600, f"Chunk line too long ({len(line)} chars): {line[:80]}..."

    # Verify roundtrip: reassemble and decode
    import base64

    b64_data = ""
    for line in b64_lines:
        parts = shlex.split(line)
        b64_data += parts[2]
    decoded = base64.b64decode(b64_data).decode()
    assert f"FLS_REGISTRY_PASSWORD={long_token}" in decoded
    assert "FLS_REGISTRY_USERNAME=serviceaccount" in decoded


def test_flash_http_url_with_oci_credentials_still_uses_direct_http_path():
    """Ensure OCI credential warning does not alter HTTP source selection."""
    client = MockFlasherClient()

    class DummyService:
        def __init__(self):
            self.storage = object()

        def start(self):
            pass

        def stop(self):
            pass

        def get_url(self):
            return "http://exporter"

    client.http = DummyService()
    client.tftp = DummyService()
    client.call = lambda *args, **kwargs: None

    captured = {}

    def capture_perform(*args):
        captured["image_url"] = args[3]
        captured["should_download_to_httpd"] = args[4]
        captured["oci_username"] = args[14]
        captured["oci_password"] = args[15]

    client._perform_flash_operation = capture_perform

    client.flash(
        "https://example.com/image.raw.xz",
        method="fls",
        oci_username="myuser",
        oci_password="mypassword",
        fls_version="",
    )

    assert captured["image_url"] == "https://example.com/image.raw.xz"
    assert captured["should_download_to_httpd"] is False
    assert captured["oci_username"] is None
    assert captured["oci_password"] is None


def test_curl_header_args_handles_quotes():
    """Test curl header formatting safely handles quotes"""
    client = MockFlasherClient()

    result = client._curl_header_args({"Authorization": "Bearer abc'def"})
    assert "'\"'\"'" in result
    assert result.startswith("-H '")
    assert result.endswith("'")


def test_flash_fails_with_invalid_headers():
    """Test flash method fails early with invalid headers"""
    client = MockFlasherClient()

    with pytest.raises(ArgumentError, match="Invalid header name 'Invalid Header': must be an HTTP token"):
        client.flash("test.raw", headers={"Invalid Header": "value"})


def test_categorize_exception_returns_non_retryable_when_present():
    """Test that non-retryable errors take priority"""
    client = MockFlasherClient()

    # Direct non-retryable error
    error = FlashNonRetryableError("Config error")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashNonRetryableError)
    assert str(result) == "Config error"


def test_categorize_exception_returns_retryable_when_present():
    """Test that retryable errors are returned"""
    client = MockFlasherClient()

    # Direct retryable error
    error = FlashRetryableError("Network timeout")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashRetryableError)
    assert str(result) == "Network timeout"


def test_categorize_exception_wraps_unknown_exceptions():
    """Test that unknown exceptions are wrapped as retryable"""
    client = MockFlasherClient()

    # Unknown exception type
    error = ValueError("Something went wrong")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashRetryableError)
    assert "ValueError" in str(result)
    assert "Something went wrong" in str(result)
    # Verify the cause chain is preserved
    assert result.__cause__ is error


def test_categorize_exception_cancelled_error_is_non_retryable():
    """Test that CancelledError is treated as non-retryable"""
    client = MockFlasherClient()

    # CancelledError should be treated as non-retryable
    error = CancelledError()
    result = client._categorize_exception(error)
    assert isinstance(result, FlashNonRetryableError)
    assert "Operation cancelled" in str(result)


def test_categorize_exception_searches_cause_chain():
    """Test that categorization searches through the cause chain"""
    client = MockFlasherClient()

    # Create a chain: generic -> generic -> retryable
    root = FlashRetryableError("Root cause")
    middle = ValueError("Middle error")
    middle.__cause__ = root
    top = RuntimeError("Top error")
    top.__cause__ = middle

    result = client._categorize_exception(top)
    assert isinstance(result, FlashRetryableError)
    assert str(result) == "Root cause"


def test_find_exception_in_chain_finds_target_type():
    """Test that _find_exception_in_chain correctly finds the target type"""
    client = MockFlasherClient()

    # Create a chain with retryable error
    retryable = FlashRetryableError("Network error")
    generic = RuntimeError("Generic error")
    generic.__cause__ = retryable

    result = client._find_exception_in_chain(generic, FlashRetryableError)
    assert result is retryable
    assert str(result) == "Network error"


def test_find_exception_in_chain_returns_none_when_not_found():
    """Test that _find_exception_in_chain returns None when target not found"""
    client = MockFlasherClient()

    error = ValueError("Some error")
    result = client._find_exception_in_chain(error, FlashRetryableError)
    assert result is None


def test_find_exception_in_chain_handles_exception_groups():
    """Test that _find_exception_in_chain searches through ExceptionGroups"""
    client = MockFlasherClient()

    # Create an ExceptionGroup with a retryable error
    retryable = FlashRetryableError("Network timeout")
    generic = ValueError("Generic error")

    # Mock an ExceptionGroup (Python 3.11+)
    class MockExceptionGroup(Exception):
        def __init__(self, message, exceptions):
            super().__init__(message)
            self.exceptions = exceptions

    group = MockExceptionGroup("Multiple errors", [generic, retryable])

    result = client._find_exception_in_chain(group, FlashRetryableError)
    assert result is retryable


def test_categorize_exception_with_nested_exception_groups():
    """Test categorization with nested ExceptionGroups"""
    client = MockFlasherClient()

    # Create nested ExceptionGroups
    non_retryable = FlashNonRetryableError("Config error")

    class MockExceptionGroup(Exception):
        def __init__(self, message, exceptions):
            super().__init__(message)
            self.exceptions = exceptions

    inner_group = MockExceptionGroup("Inner errors", [non_retryable])
    outer_group = MockExceptionGroup("Outer errors", [ValueError("Other"), inner_group])

    result = client._categorize_exception(outer_group)
    assert isinstance(result, FlashNonRetryableError)
    assert str(result) == "Config error"


def test_categorize_exception_preserves_cause_for_wrapped_exceptions():
    """Test that wrapped unknown exceptions preserve the cause chain"""
    client = MockFlasherClient()

    original = IOError("File not found")
    result = client._categorize_exception(original)

    assert isinstance(result, FlashRetryableError)
    assert result.__cause__ is original
    # IOError is an alias for OSError in Python 3
    assert "OSError" in str(result) or "IOError" in str(result)
    assert "File not found" in str(result)


def test_resolve_flash_parameters():
    """Test flash parameter resolution for single file, partitions, and error cases"""
    client = MockFlasherClient()

    assert client._resolve_flash_parameters("image.img", None, None) == [("image.img", None, None)]
    assert client._resolve_flash_parameters("image.img", None, "emmc") == [("image.img", None, "emmc")]
    assert client._resolve_flash_parameters(None, ("rootfs:rootfs.img", "boot:boot.img"), "emmc") == [
        ("rootfs.img", "rootfs", "emmc"),
        ("boot.img", "boot", "emmc"),
    ]

    with pytest.raises(click.UsageError):
        client._resolve_flash_parameters("image.img", ("rootfs:rootfs.img",), None)
    with pytest.raises(click.UsageError):
        client._resolve_flash_parameters(None, None, None)
    with pytest.raises(click.UsageError):
        client._resolve_flash_parameters(None, ("rootfs_no_colon",), None)
