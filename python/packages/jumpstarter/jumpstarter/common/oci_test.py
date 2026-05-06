import base64
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from .oci import (
    _normalize_registry,
    parse_oci_registry,
    read_auth_file_credentials,
    resolve_oci_credentials,
)


class TestParseOciRegistry:
    @pytest.mark.parametrize(
        "oci_url,expected",
        [
            ("oci://quay.io/org/image:latest", "quay.io"),
            ("oci://registry.example.com/org/image:v1", "registry.example.com"),
            ("oci://registry.example.com:5000/org/image:v1", "registry.example.com:5000"),
            ("oci://ghcr.io/user/repo:sha256-abc", "ghcr.io"),
            ("oci://docker.io/library/ubuntu:22.04", "docker.io"),
            # Without oci:// prefix
            ("quay.io/org/image:latest", "quay.io"),
            ("registry.example.com:5000/repo:tag", "registry.example.com:5000"),
        ],
    )
    def test_standard_urls(self, oci_url, expected):
        assert parse_oci_registry(oci_url) == expected

    def test_bare_image_name_defaults_to_docker_hub(self):
        # "ubuntu:latest" has no slash — it's a Docker Hub shorthand
        assert parse_oci_registry("oci://ubuntu:latest") == "docker.io"


class TestNormalizeRegistry:
    @pytest.mark.parametrize(
        "registry,expected",
        [
            ("quay.io", "quay.io"),
            ("docker.io", "docker.io"),
            ("index.docker.io", "docker.io"),
            ("registry-1.docker.io", "docker.io"),
            ("registry.hub.docker.com", "docker.io"),
            ("https://index.docker.io/v1/", "docker.io"),
            ("https://registry.example.com/", "registry.example.com"),
            ("registry.example.com:5000", "registry.example.com:5000"),
        ],
    )
    def test_normalization(self, registry, expected):
        assert _normalize_registry(registry) == expected


def _make_auth_json(auths: dict) -> str:
    """Helper to create auth.json content."""
    return json.dumps({"auths": auths})


def _encode_auth(username: str, password: str) -> str:
    """Helper to create base64-encoded auth string."""
    return base64.b64encode(f"{username}:{password}".encode()).decode()


class TestReadAuthFileCredentials:
    def test_reads_from_docker_config(self, tmp_path):
        docker_dir = tmp_path / ".docker"
        docker_dir.mkdir()
        config_path = docker_dir / "config.json"
        config_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("myuser", "mypass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[config_path],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username == "myuser"
            assert password == "mypass"

    def test_reads_from_podman_auth_json(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"ghcr.io": {"auth": _encode_auth("ghuser", "ghtoken")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://ghcr.io/org/repo:v1")
            assert username == "ghuser"
            assert password == "ghtoken"

    def test_handles_docker_hub_url_variants(self, tmp_path):
        """Docker Hub credentials stored under various key formats should match."""
        auth_path = tmp_path / "config.json"
        auth_path.write_text(
            _make_auth_json({"https://index.docker.io/v1/": {"auth": _encode_auth("dockuser", "dockpass")}})
        )

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://docker.io/library/ubuntu:22.04")
            assert username == "dockuser"
            assert password == "dockpass"

    def test_returns_none_when_no_match(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://ghcr.io/org/repo:v1")
            assert username is None
            assert password is None

    def test_returns_none_when_no_auth_files_exist(self):
        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[Path("/nonexistent/path/auth.json")],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username is None
            assert password is None

    def test_skips_malformed_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")

        good_file = tmp_path / "good.json"
        good_file.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[bad_file, good_file],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username == "user"
            assert password == "pass"

    def test_supports_separate_username_password_fields(self, tmp_path):
        """Some tools write username/password directly instead of base64 auth."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"username": "altuser", "password": "altpass"}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username == "altuser"
            assert password == "altpass"

    def test_first_matching_file_wins(self, tmp_path):
        """When multiple auth files have credentials, the first one wins."""
        first = tmp_path / "first.json"
        first.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("first_user", "first_pass")}}))
        second = tmp_path / "second.json"
        second.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("second_user", "second_pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[first, second],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username == "first_user"
            assert password == "first_pass"

    def test_password_with_colon(self, tmp_path):
        """Passwords containing colons should be handled correctly."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass:with:colons")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username == "user"
            assert password == "pass:with:colons"

    def test_registry_with_port(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"registry.local:5000": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://registry.local:5000/myrepo:latest")
            assert username == "user"
            assert password == "pass"

    def test_empty_auths_section(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(json.dumps({"auths": {}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            username, password = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert username is None
            assert password is None


class TestResolveOciCredentials:
    def test_env_vars_take_priority(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                username, password = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert username == "envuser"
                assert password == "envpass"

    def test_falls_back_to_auth_file(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        with patch.dict(os.environ, env_clean, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                username, password = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert username == "fileuser"
                assert password == "filepass"

    def test_partial_env_falls_back_to_auth_file(self, tmp_path):
        """When only one env var is set, fall through to auth file instead of returning partial."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        # Only OCI_USERNAME set, OCI_PASSWORD not set
        env_partial = {k: v for k, v in os.environ.items() if k != "OCI_PASSWORD"}
        env_partial["OCI_USERNAME"] = "partialuser"
        with patch.dict(os.environ, env_partial, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                username, password = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert username == "fileuser"
                assert password == "filepass"

    def test_returns_none_when_no_source(self):
        env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        with patch.dict(os.environ, env_clean, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[]):
                username, password = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert username is None
                assert password is None
