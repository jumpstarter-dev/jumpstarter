import inspect
import shlex
import threading
from concurrent.futures import CancelledError
from contextlib import nullcontext
from itertools import pairwise
from pathlib import PosixPath
from types import SimpleNamespace

import click
import pytest

from .client import DEFAULT_FLS_VERSION, BaseFlasherClient, FlashError, FlashNonRetryableError, FlashRetryableError
from jumpstarter.client.core import DriverError, DriverMethodNotImplemented
from jumpstarter.common.exceptions import ArgumentError, ConfigurationError


class MockFlasherClient(BaseFlasherClient):
    """Mock client for testing without full initialization"""

    def __init__(self):
        self._manifest = None
        self._console_debug = False
        self._redaction_values = set()
        self.children = {}
        self.methods_description = {}
        self.logger = type(  # ty: ignore[invalid-assignment]
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


def test_resolve_oci_credentials_fails_when_partial():
    """Test OCI credential resolution fails when only one value is provided"""
    client = MockFlasherClient()

    with pytest.raises(click.ClickException, match="OCI authentication requires both"):
        client._resolve_oci_credentials("oci://quay.io/org/image:tag", "myuser", None)

    with pytest.raises(click.ClickException, match="OCI authentication requires both"):
        client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, "mypassword")


def test_resolve_oci_credentials_accepts_pair_and_strips_whitespace():
    """Test OCI credential resolution accepts full username/password pair and strips whitespace"""
    client = MockFlasherClient()

    creds = client._resolve_oci_credentials("oci://quay.io/org/image:tag", " myuser ", " mypassword ")
    assert creds.username == "myuser"
    assert creds.plain_password == "mypassword"


def test_resolve_oci_credentials_reads_env_for_oci_path(monkeypatch):
    """Test OCI credentials are read from environment for OCI paths."""
    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.setenv("OCI_PASSWORD", "env-pass")

    creds = client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, None)
    assert creds.username == "env-user"
    assert creds.plain_password == "env-pass"


def test_resolve_oci_credentials_ignores_env_for_non_oci_path(monkeypatch):
    """Test OCI credential env vars are ignored for non-OCI image paths."""
    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.setenv("OCI_PASSWORD", "env-pass")

    creds = client._resolve_oci_credentials("https://example.com/image.raw.xz", None, None)
    assert creds.username is None
    assert creds.password is None


def test_resolve_oci_credentials_partial_env_falls_through_to_auth_file(monkeypatch):
    """Partial env vars should fall through to auth file lookup, not error."""
    from unittest.mock import patch

    from pydantic import SecretStr

    from jumpstarter.common.oci import OciCredentials

    client = MockFlasherClient()
    monkeypatch.setenv("OCI_USERNAME", "env-user")
    monkeypatch.delenv("OCI_PASSWORD", raising=False)

    # When auth file has no match, result is unauthenticated — no error
    with patch("jumpstarter.common.oci.read_auth_file_credentials", return_value=OciCredentials()):
        creds = client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, None)
        assert creds.username is None
        assert creds.password is None

    # When auth file has a match, those credentials are used
    with patch(
        "jumpstarter.common.oci.read_auth_file_credentials",
        return_value=OciCredentials(username="fileuser", password=SecretStr("filepass")),
    ):
        creds = client._resolve_oci_credentials("oci://quay.io/org/image:tag", None, None)
        assert creds.username == "fileuser"
        assert creds.plain_password == "filepass"


def test_resolve_oci_credentials_normalizes_empty_strings(monkeypatch):
    """Empty-string username/password should be treated as absent and fall through."""
    from unittest.mock import patch

    from jumpstarter.common.oci import OciCredentials

    client = MockFlasherClient()
    monkeypatch.delenv("OCI_USERNAME", raising=False)
    monkeypatch.delenv("OCI_PASSWORD", raising=False)

    with patch("jumpstarter.common.oci.read_auth_file_credentials", return_value=OciCredentials()) as mock_auth:
        creds = client._resolve_oci_credentials("oci://quay.io/org/image:tag", "", "")
        assert creds.username is None
        assert creds.password is None
        mock_auth.assert_called_once()


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


class _IdleService:
    def start(self):
        pass

    def stop(self):
        pass


def _raised_from(*errors):
    """The first error, raised from the second, raised from the third, and so on"""
    for error, cause in pairwise(errors):
        error.__cause__ = cause
    return errors[0]


@pytest.mark.parametrize(
    "error, expected",
    [
        pytest.param(
            RuntimeError("Failed to get U-Boot prompt"),
            "Flash operation failed after 1 attempts (Caused by: Failed to get U-Boot prompt)",
            id="unknown-is-retried",
        ),
        pytest.param(
            FlashRetryableError("Network timeout"),
            "Flash operation failed after 1 attempts (Caused by: Network timeout)",
            id="retryable-is-retried",
        ),
        pytest.param(
            FlashNonRetryableError("Could not find dd process ID"),
            "Flash operation failed (Caused by: Could not find dd process ID)",
            id="non-retryable",
        ),
        pytest.param(
            _raised_from(
                RuntimeError("Flasher session failed"), ValueError("Bad response"), FlashNonRetryableError("Config")
            ),
            "Flash operation failed (Caused by: Flasher session failed)",
            id="non-retryable-in-cause-chain",
        ),
        pytest.param(
            ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [
                    FlashRetryableError("Network timeout"),
                    ExceptionGroup("unhandled errors in a TaskGroup", [FlashNonRetryableError("Config")]),
                ],
            ),
            (
                "Flash operation failed"
                " (Caused by: FlashRetryableError: Network timeout; FlashNonRetryableError: Config)"
            ),
            id="non-retryable-wins-in-nested-groups",
        ),
        pytest.param(
            _raised_from(
                RuntimeError("Flasher session failed"),
                ExceptionGroup("unhandled errors in a TaskGroup", [FlashNonRetryableError("Config")]),
            ),
            "Flash operation failed (Caused by: Flasher session failed)",
            id="non-retryable-in-group-cause",
        ),
        pytest.param(
            ConfigurationError("Target emmc not found in manifest"),
            "Flash operation failed (Caused by: Target emmc not found in manifest)",
            id="configuration-error",
        ),
        pytest.param(CancelledError(), "Flash operation failed (Caused by: CancelledError)", id="cancelled"),
        pytest.param(
            ExceptionGroup(
                "unhandled errors in a TaskGroup",
                [
                    RuntimeError("Failed to get U-Boot prompt"),
                    ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("handler failed")]),
                ],
            ),
            (
                "Flash operation failed after 1 attempts"
                " (Caused by: RuntimeError: Failed to get U-Boot prompt; ValueError: handler failed)"
            ),
            id="unknown-group-is-retried",
        ),
    ],
)
def test_flash_retries_only_retryable_failures_and_reports_them_once(error, expected):
    """A non-retryable failure anywhere in the cause chain or an exception group fails the flash at once.

    Anything else is retried, which "after N attempts" shows. FlashError names the
    underlying failure once, and chains it as __cause__.
    """
    client = MockFlasherClient()
    client.http = _IdleService()  # ty: ignore[missing-attribute]
    client.tftp = _IdleService()  # ty: ignore[missing-attribute]
    client.call = lambda *args, **kwargs: None

    def fail(*args, **kwargs):
        raise error

    client._perform_flash_operation = fail

    with pytest.raises(FlashError) as excinfo:
        client.flash("https://example.com/image.raw.xz", retries=0)

    assert str(excinfo.value) == expected
    assert excinfo.value.__cause__ is error


def test_flash_does_not_retry_onto_a_failed_upload(tmp_path):
    """A retry must not flash whatever the exporter holds after the image upload failed"""
    image = tmp_path / "image.raw.xz"
    image.write_bytes(b"new image")
    first_attempt_started = threading.Event()

    class FailingStorage:
        def exists(self, filename):
            return False

        def write_from_path(self, filename, path, operator):
            # The upload drops while the first attempt boots the flasher
            first_attempt_started.wait(timeout=10)
            raise ConnectionError("upload dropped")

    class Http(_IdleService):
        storage = FailingStorage()

        def get_url(self):
            return "http://exporter"

    def resolve_target(*args):
        first_attempt_started.set()
        return "/dev/mmcblk0"

    flashed = []
    console = SimpleNamespace(sendline=lambda *args: None, expect=lambda *args, **kwargs: None)
    client = MockFlasherClient()
    client.http = Http()  # ty: ignore[missing-attribute]
    client.tftp = _IdleService()  # ty: ignore[missing-attribute]
    client.call = lambda *args, **kwargs: None
    client._manifest = SimpleNamespace(
        spec=SimpleNamespace(login=SimpleNamespace(prompt="#"), preflash_commands=[])
    )
    client._dhcp_details = SimpleNamespace(ip_address="192.0.2.2", gateway="192.0.2.1")
    client._busybox = lambda: nullcontext(console)  # ty: ignore[invalid-assignment]
    client._resolve_and_prepare_target = resolve_target  # ty: ignore[invalid-assignment]
    client._flash_with_fls = lambda *args: flashed.append(args)  # ty: ignore[invalid-assignment]

    with pytest.raises(FlashError) as excinfo:
        client.flash(str(image), retries=1, power_off=False)

    assert flashed == []
    assert str(excinfo.value) == (
        "Flash operation failed (Caused by: Uploading the image to the exporter failed (Caused by: upload dropped))"
    )


def test_filename_strips_query_params_from_url_path():
    """Test _filename strips query parameters from paths with signed URL params"""
    client = MockFlasherClient()

    # Full HTTP URL
    assert client._filename("https://cdn.example.com/images/image.raw.xz") == "image.raw.xz"

    # Full HTTP URL with query parameters (e.g. CloudFront signed URL)
    assert (
        client._filename("https://cdn.example.com/images/image.raw.xz?Expires=123&Signature=abc&Key-Pair-Id=xyz")
        == "image.raw.xz"
    )

    # Path string with query parameters (e.g. from path_with_query in bearer token path)
    assert client._filename("/images/image.raw.xz?Expires=123&Signature=abc") == "image.raw.xz"

    # Plain path without query parameters
    assert client._filename("/images/image.raw.xz") == "image.raw.xz"

    # OCI path
    assert client._filename("oci://quay.io/org/myimage:latest") == "myimage-latest"


def test_decompression_command_with_query_params():
    """Test _get_decompression_command handles paths with query parameters"""
    from pathlib import PosixPath

    from .client import _get_decompression_command

    # Standard PosixPath
    assert _get_decompression_command(PosixPath("/images/image.raw.xz")) == "xzcat |"
    assert _get_decompression_command(PosixPath("/images/image.raw.gz")) == "zcat |"
    assert _get_decompression_command(PosixPath("/images/image.raw")) == ""

    # Full HTTP URL
    assert _get_decompression_command("https://cdn.example.com/images/image.raw.xz") == "xzcat |"

    # Zstandard compression
    assert _get_decompression_command(PosixPath("/images/image.raw.zst")) == "zstdcat |"
    assert _get_decompression_command("https://cdn.example.com/images/image.raw.zst") == "zstdcat |"

    # String path with query parameters (e.g. from path_with_query in bearer token path)
    assert _get_decompression_command("/images/image.raw.xz?Expires=123&Signature=abc") == "xzcat |"
    assert _get_decompression_command("/images/image.raw.gz?Expires=123") == "zcat |"
    assert _get_decompression_command("/images/image.raw.zst?Expires=123") == "zstdcat |"
    assert _get_decompression_command("/images/image.raw?Expires=123") == ""


class _Http(_IdleService):
    storage = object()

    def get_url(self):
        return "http://exporter"


def _client_capturing_the_flash_operation():
    """A client whose flash() hands each attempt's arguments, by name, to the returned dict instead of a DUT"""
    client = MockFlasherClient()
    client.http = _Http()  # ty: ignore[missing-attribute]
    client.tftp = _IdleService()  # ty: ignore[missing-attribute]
    client.call = lambda *args, **kwargs: None
    captured = {}
    signature = inspect.signature(BaseFlasherClient._perform_flash_operation)

    def capture(*args, **kwargs):
        captured.update(signature.bind(client, *args, **kwargs).arguments)

    client._perform_flash_operation = capture
    return client, captured


@pytest.mark.parametrize(
    "url, options",
    [
        pytest.param("https://cdn.example.com/images/image.raw.xz?Expires=123&Signature=abc&Key-Pair-Id=xyz", {},
                     id="signed-url"),
        # OCI credentials only apply to oci:// images, so they are dropped
        pytest.param("https://example.com/image.raw.xz", {"oci_username": "myuser", "oci_password": "mypassword"},
                     id="oci-credentials"),
    ],
)
def test_flash_has_the_dut_download_an_http_url_as_given(url, options):
    client, captured = _client_capturing_the_flash_operation()

    client.flash(url, method="fls", fls_version="", **options)

    assert (captured["image_url"], captured["upload"]) == (url, None)
    assert (captured["oci_username"], captured["oci_password"]) == (None, None)


def test_flash_fetches_a_bearer_token_url_through_the_exporter():
    """The exporter fetches the image with its query string, and the DUT downloads it by its plain name"""
    client, captured = _client_capturing_the_flash_operation()
    client._transfer_bg_thread = lambda *args, **kwargs: None

    signed_url = "https://cdn.example.com/images/image.raw.xz?Expires=123&Signature=abc&Key-Pair-Id=xyz"
    client.flash(signed_url, force_exporter_http=True, bearer_token="test-token-123", method="fls", fls_version="")

    assert captured["upload"] is not None
    assert captured["path"] == "/images/image.raw.xz?Expires=123&Signature=abc&Key-Pair-Id=xyz"
    assert captured["image_url"] == "http://exporter/image.raw.xz"


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


_NOT_IMPLEMENTED = object()


def _github(version):
    return f"https://github.com/jumpstarter-dev/fls/releases/download/{version}/fls-aarch64-linux-musl"


def _fls_client(exporter_version=None, serve_error=None):
    """Client whose exporter reports exporter_version and can (or cannot) serve its own fls."""
    client = MockFlasherClient()
    calls, downloads = [], []

    class Http:
        def get_url(self):
            return "http://exporter:8080"

    def call(method, *args):
        calls.append(method)
        if method == "get_fls_version":
            if exporter_version is _NOT_IMPLEMENTED:
                raise DriverMethodNotImplemented("get_fls_version")
            return exporter_version
        if method == "setup_fls_binary":
            if serve_error is not None:
                raise serve_error
            return None
        raise AssertionError(f"unexpected call {method}")

    client.http = Http()  # ty: ignore[missing-attribute]
    client.call = call
    client._download_fls_binary = lambda console, prompt, url, msg: downloads.append(url)  # ty: ignore[invalid-assignment]
    return client, calls, downloads


@pytest.mark.parametrize(
    "fls_version, fls_binary_url, exporter_version, serve_error, expected_url, expected_calls",
    [
        # caller overrides always win, whatever the exporter is configured with
        (None, "http://mirror/fls", "0.9.9", None, "http://mirror/fls", []),
        ("0.2.0", None, "0.9.9", None, _github("0.2.0"), []),
        ("0.2.0", "http://mirror/fls", "0.9.9", None, "http://mirror/fls", []),
        # no caller choice: the exporter decides
        (None, None, None, None, "http://exporter:8080/fls", ["get_fls_version", "setup_fls_binary"]),
        (None, None, "0.4.0", None, _github("0.4.0"), ["get_fls_version"]),
        # exporter has no binary, or predates get_fls_version: previous default from GitHub
        (None, None, None, DriverError("fls binary not found"), _github(DEFAULT_FLS_VERSION),
         ["get_fls_version", "setup_fls_binary"]),
        (None, None, _NOT_IMPLEMENTED, None, _github(DEFAULT_FLS_VERSION), ["get_fls_version"]),
        # "" requires the exporter's binary
        ("", None, "0.9.9", None, "http://exporter:8080/fls", ["setup_fls_binary"]),
    ],
)
def test_install_fls_precedence(fls_version, fls_binary_url, exporter_version, serve_error, expected_url,
                                expected_calls):
    client, calls, downloads = _fls_client(exporter_version, serve_error)
    client._install_fls(None, "#", fls_version, fls_binary_url)
    assert downloads == [expected_url]
    assert calls == expected_calls


def test_install_fls_empty_version_has_no_fallback():
    client, _, downloads = _fls_client(serve_error=DriverError("fls binary not found"))
    with pytest.raises(FlashNonRetryableError, match="fls binary not found"):
        client._install_fls(None, "#", "", None)
    assert downloads == []


def test_flash_cli_fls_version_defaults_to_exporter_choice():
    client = MockFlasherClient()
    captured = {}
    client.flash = lambda *args, **kwargs: captured.update(kwargs)
    client.call = lambda *args, **kwargs: None

    from click.testing import CliRunner

    result = CliRunner().invoke(client.cli(), ["flash", "http://example.com/image.raw"])
    assert result.exit_code == 0, result.output
    assert captured["fls_version"] is None
