import base64
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from .oci import (
    OciCredentials,
    _get_auth_file_paths,
    _get_unqualified_search_registries,
    _normalize_registry,
    _parse_registries_for_url,
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
        # "ubuntu:latest" has no slash — defaults to first unqualified-search-registry
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=["docker.io"],
        ):
            assert parse_oci_registry("oci://ubuntu:latest") == "docker.io"

    def test_bare_image_uses_configured_registry(self):
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=["registry.example.com", "docker.io"],
        ):
            assert parse_oci_registry("oci://ubuntu:latest") == "registry.example.com"
            assert parse_oci_registry("oci://myimage") == "registry.example.com"


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
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "myuser"
            assert result.password.get_secret_value() == "mypass"

    def test_reads_from_podman_auth_json(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"ghcr.io": {"auth": _encode_auth("ghuser", "ghtoken")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://ghcr.io/org/repo:v1")
            assert result.username == "ghuser"
            assert result.password.get_secret_value() == "ghtoken"

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
            result = read_auth_file_credentials("oci://docker.io/library/ubuntu:22.04")
            assert result.username == "dockuser"
            assert result.password.get_secret_value() == "dockpass"

    def test_returns_none_when_no_match(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://ghcr.io/org/repo:v1")
            assert result.username is None
            assert result.password is None

    def test_returns_none_when_no_auth_files_exist(self):
        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[Path("/nonexistent/path/auth.json")],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None

    def test_skips_malformed_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")

        good_file = tmp_path / "good.json"
        good_file.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[bad_file, good_file],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "user"
            assert result.password.get_secret_value() == "pass"

    def test_supports_separate_username_password_fields(self, tmp_path):
        """Some tools write username/password directly instead of base64 auth."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"username": "altuser", "password": "altpass"}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "altuser"
            assert result.password.get_secret_value() == "altpass"

    def test_whitespace_only_separate_fields_skipped(self, tmp_path):
        """Whitespace-only password in separate fields should be skipped, not crash."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"username": "user", "password": "   "}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None

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
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "first_user"
            assert result.password.get_secret_value() == "first_pass"

    def test_password_with_colon(self, tmp_path):
        """Passwords containing colons should be handled correctly."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass:with:colons")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "user"
            assert result.password.get_secret_value() == "pass:with:colons"

    def test_registry_with_port(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"registry.local:5000": {"auth": _encode_auth("user", "pass")}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://registry.local:5000/myrepo:latest")
            assert result.username == "user"
            assert result.password.get_secret_value() == "pass"

    def test_empty_auths_section(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(json.dumps({"auths": {}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None


class TestResolveOciCredentials:
    def test_env_vars_take_priority(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        with patch.dict(os.environ, {"OCI_USERNAME": "envuser", "OCI_PASSWORD": "envpass"}):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username == "envuser"
                assert result.password.get_secret_value() == "envpass"

    def test_falls_back_to_auth_file(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        with patch.dict(os.environ, env_clean, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username == "fileuser"
                assert result.password.get_secret_value() == "filepass"

    def test_partial_env_falls_back_to_auth_file(self, tmp_path):
        """When only one env var is set, fall through to auth file instead of returning partial."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        # Only OCI_USERNAME set, OCI_PASSWORD not set
        env_partial = {k: v for k, v in os.environ.items() if k != "OCI_PASSWORD"}
        env_partial["OCI_USERNAME"] = "partialuser"
        with patch.dict(os.environ, env_partial, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username == "fileuser"
                assert result.password.get_secret_value() == "filepass"

    def test_partial_env_password_only_falls_back_to_auth_file(self, tmp_path):
        """When only OCI_PASSWORD is set, fall through to auth file instead of returning partial."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        env_partial = {k: v for k, v in os.environ.items() if k != "OCI_USERNAME"}
        env_partial["OCI_PASSWORD"] = "partialpass"
        with patch.dict(os.environ, env_partial, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username == "fileuser"
                assert result.password.get_secret_value() == "filepass"

    def test_whitespace_env_vars_fall_through_to_auth_file(self, tmp_path):
        """Whitespace-only env vars should not be treated as credentials."""
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("fileuser", "filepass")}}))

        env = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        env["OCI_USERNAME"] = "  "
        env["OCI_PASSWORD"] = "  "
        with patch.dict(os.environ, env, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username == "fileuser"
                assert result.password.get_secret_value() == "filepass"

    def test_returns_none_when_no_source(self):
        env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        with patch.dict(os.environ, env_clean, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert result.username is None
                assert result.password is None


class TestParseOciRegistryDigest:
    """Digest references (image@sha256:...) must not corrupt registry parsing."""

    def test_bare_image_with_digest(self):
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=["docker.io"],
        ):
            assert parse_oci_registry("oci://ubuntu@sha256:abc123") == "docker.io"

    def test_image_with_path_and_digest(self):
        assert parse_oci_registry("oci://quay.io/org/repo@sha256:abc123") == "quay.io"

    def test_registry_port_with_digest(self):
        assert parse_oci_registry("oci://registry.local:5000/repo@sha256:abc") == "registry.local:5000"


class TestGetAuthFilePaths:
    """Verify _get_auth_file_paths reads env vars and produces correct ordering."""

    def test_default_paths_without_env_vars(self):
        env_clean = {
            k: v for k, v in os.environ.items() if k not in ("REGISTRY_AUTH_FILE", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
        }
        with patch.dict(os.environ, env_clean, clear=True):
            paths = _get_auth_file_paths()
            path_strs = [str(p) for p in paths]
            assert any(".config/containers/auth.json" in p for p in path_strs)
            assert any(".docker/config.json" in p for p in path_strs)

    def test_registry_auth_file_takes_priority(self, tmp_path):
        custom_path = str(tmp_path / "custom-auth.json")
        env = {
            k: v for k, v in os.environ.items() if k not in ("REGISTRY_AUTH_FILE", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
        }
        env["REGISTRY_AUTH_FILE"] = custom_path
        with patch.dict(os.environ, env, clear=True):
            paths = _get_auth_file_paths()
            assert paths[0] == Path(custom_path)

    def test_xdg_runtime_dir_adds_podman_path(self, tmp_path):
        env = {
            k: v for k, v in os.environ.items() if k not in ("REGISTRY_AUTH_FILE", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
        }
        env["XDG_RUNTIME_DIR"] = str(tmp_path)
        with patch.dict(os.environ, env, clear=True):
            paths = _get_auth_file_paths()
            assert Path(tmp_path / "containers" / "auth.json") in paths

    def test_docker_config_adds_custom_docker_path(self, tmp_path):
        docker_dir = str(tmp_path / "mydocker")
        env = {
            k: v for k, v in os.environ.items() if k not in ("REGISTRY_AUTH_FILE", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
        }
        env["DOCKER_CONFIG"] = docker_dir
        with patch.dict(os.environ, env, clear=True):
            paths = _get_auth_file_paths()
            assert Path(docker_dir) / "config.json" in paths

    def test_all_env_vars_set_produces_correct_order(self, tmp_path):
        env = {
            k: v for k, v in os.environ.items() if k not in ("REGISTRY_AUTH_FILE", "XDG_RUNTIME_DIR", "DOCKER_CONFIG")
        }
        env["REGISTRY_AUTH_FILE"] = str(tmp_path / "explicit.json")
        env["XDG_RUNTIME_DIR"] = str(tmp_path / "xdg")
        env["DOCKER_CONFIG"] = str(tmp_path / "dockercfg")
        with patch.dict(os.environ, env, clear=True):
            paths = _get_auth_file_paths()
            path_strs = [str(p) for p in paths]
            # REGISTRY_AUTH_FILE first, then XDG, then ~/.config, then DOCKER_CONFIG, then ~/.docker
            assert path_strs[0] == str(tmp_path / "explicit.json")
            assert "xdg/containers/auth.json" in path_strs[1]
            assert ".config/containers/auth.json" in path_strs[2]
            assert "dockercfg/config.json" in path_strs[3]
            assert ".docker/config.json" in path_strs[4]


class TestInvalidBase64Auth:
    """Malformed base64 in auth entries should be skipped without crashing."""

    def test_garbage_base64_skipped(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": "not-valid-base64!!!"}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None

    def test_garbage_base64_falls_through_to_separate_fields(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            _make_auth_json(
                {"quay.io": {"auth": "not-valid!!!", "username": "fallback_user", "password": "fallback_pass"}}
            )
        )

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username == "fallback_user"
            assert result.password.get_secret_value() == "fallback_pass"

    def test_empty_username_in_base64_falls_through(self, tmp_path):
        """Base64 encoding of ':password' should not return ('', 'password')."""
        auth_path = tmp_path / "auth.json"
        auth_b64 = base64.b64encode(b":onlypassword").decode()
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": auth_b64}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None

    def test_empty_password_in_base64_falls_through(self, tmp_path):
        """Base64 encoding of 'username:' should not return ('username', '')."""
        auth_path = tmp_path / "auth.json"
        auth_b64 = base64.b64encode(b"onlyusername:").decode()
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": auth_b64}}))

        with patch(
            "jumpstarter.common.oci._get_auth_file_paths",
            return_value=[auth_path],
        ):
            result = read_auth_file_credentials("oci://quay.io/org/image:latest")
            assert result.username is None
            assert result.password is None


class TestOciCredentials:
    def test_fields(self):
        creds = OciCredentials(username="user", password="pass")
        assert creds.username == "user"
        assert creds.password.get_secret_value() == "pass"

    def test_plain_password(self):
        creds = OciCredentials(username="user", password="pass")
        assert creds.plain_password == "pass"
        assert OciCredentials().plain_password is None

    def test_is_authenticated(self):
        assert OciCredentials(username="user", password="pass").is_authenticated
        assert not OciCredentials().is_authenticated
        assert not OciCredentials(username=None, password=None).is_authenticated

    def test_rejects_asymmetric_at_construction(self):
        with pytest.raises(ValueError, match="both username and password"):
            OciCredentials(username="user", password=None)
        with pytest.raises(ValueError, match="both username and password"):
            OciCredentials(username=None, password="pass")

    def test_empty_strings_normalized_to_none(self):
        creds = OciCredentials(username="", password="")
        assert creds.username is None
        assert creds.password is None
        assert not creds.is_authenticated

    def test_username_with_empty_password_rejected(self):
        with pytest.raises(ValueError, match="both username and password"):
            OciCredentials(username="user", password="")

    def test_whitespace_strings_normalized_to_none(self):
        creds = OciCredentials(username="  ", password="  ")
        assert creds.username is None
        assert creds.password is None
        assert not creds.is_authenticated

    def test_strips_whitespace_from_credentials(self):
        creds = OciCredentials(username=" user ", password=" pass ")
        assert creds.username == "user"
        assert creds.password.get_secret_value() == "pass"

    def test_frozen(self):
        creds = OciCredentials(username="user", password="pass")
        with pytest.raises(ValidationError):
            creds.username = "other"

    def test_resolve_returns_oci_credentials_type(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"quay.io": {"auth": _encode_auth("user", "pass")}}))

        env_clean = {k: v for k, v in os.environ.items() if k not in ("OCI_USERNAME", "OCI_PASSWORD")}
        with patch.dict(os.environ, env_clean, clear=True):
            with patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_path]):
                result = resolve_oci_credentials("oci://quay.io/org/image:latest")
                assert isinstance(result, OciCredentials)
                assert result.is_authenticated


class TestUnqualifiedSearchRegistries:
    """Verify registries.conf reading for bare image resolution."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _get_unqualified_search_registries.cache_clear()
        yield
        _get_unqualified_search_registries.cache_clear()

    def test_falls_back_to_docker_io_when_no_config(self, tmp_path):
        with patch(
            "jumpstarter.common.oci._get_registries_conf_paths",
            return_value=[tmp_path / "nonexistent.conf"],
        ):
            result = _get_unqualified_search_registries()
            assert result == ("docker.io",)

    def test_reads_from_registries_conf(self, tmp_path):
        conf = tmp_path / "registries.conf"
        conf.write_text('unqualified-search-registries = ["quay.io", "docker.io"]\n')

        with patch(
            "jumpstarter.common.oci._get_registries_conf_paths",
            return_value=[conf],
        ):
            result = _get_unqualified_search_registries()
            assert result == ("quay.io", "docker.io")

    def test_skips_malformed_toml(self, tmp_path):
        bad = tmp_path / "bad.conf"
        bad.write_text("this is not valid toml [[[")

        good = tmp_path / "good.conf"
        good.write_text('unqualified-search-registries = ["registry.example.com"]\n')

        with patch(
            "jumpstarter.common.oci._get_registries_conf_paths",
            return_value=[bad, good],
        ):
            result = _get_unqualified_search_registries()
            assert result == ("registry.example.com",)

    def test_skips_config_without_key(self, tmp_path):
        conf = tmp_path / "registries.conf"
        conf.write_text('[registries.search]\nregistries = ["old-format"]\n')

        with patch(
            "jumpstarter.common.oci._get_registries_conf_paths",
            return_value=[conf],
        ):
            result = _get_unqualified_search_registries()
            assert result == ("docker.io",)


class TestParseRegistriesForUrl:
    """Verify _parse_registries_for_url returns correct registry lists."""

    def test_explicit_registry_returns_single(self):
        assert _parse_registries_for_url("oci://quay.io/org/image:tag") == ("quay.io",)
        assert _parse_registries_for_url("oci://ghcr.io/user/repo:v1") == ("ghcr.io",)

    def test_registry_with_port_returns_single(self):
        assert _parse_registries_for_url("oci://registry.local:5000/repo:tag") == ("registry.local:5000",)

    def test_bare_image_returns_all_configured(self):
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=("quay.io", "docker.io"),
        ):
            result = _parse_registries_for_url("oci://ubuntu:latest")
            assert result == ("quay.io", "docker.io")

    def test_bare_image_no_tag_returns_all_configured(self):
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=("registry.example.com",),
        ):
            result = _parse_registries_for_url("oci://myimage")
            assert result == ("registry.example.com",)

    def test_namespace_image_returns_all_configured(self):
        with patch(
            "jumpstarter.common.oci._get_unqualified_search_registries",
            return_value=("quay.io", "docker.io"),
        ):
            result = _parse_registries_for_url("oci://library/ubuntu")
            assert result == ("quay.io", "docker.io")

    def test_localhost_returns_single(self):
        assert _parse_registries_for_url("localhost/myrepo:tag") == ("localhost",)


class TestBareImageCredentialLookup:
    """Verify credential lookup tries all configured registries for bare images."""

    def test_finds_credentials_from_secondary_registry(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"docker.io": {"auth": _encode_auth("dockuser", "dockpass")}}))

        with (
            patch(
                "jumpstarter.common.oci._get_unqualified_search_registries",
                return_value=["quay.io", "docker.io"],
            ),
            patch(
                "jumpstarter.common.oci._get_auth_file_paths",
                return_value=[auth_path],
            ),
        ):
            result = read_auth_file_credentials("oci://ubuntu:latest")
            assert result.username == "dockuser"
            assert result.password.get_secret_value() == "dockpass"

    def test_first_matching_registry_wins(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(
            _make_auth_json(
                {
                    "quay.io": {"auth": _encode_auth("quayuser", "quaypass")},
                    "docker.io": {"auth": _encode_auth("dockuser", "dockpass")},
                }
            )
        )

        with (
            patch(
                "jumpstarter.common.oci._get_unqualified_search_registries",
                return_value=["quay.io", "docker.io"],
            ),
            patch(
                "jumpstarter.common.oci._get_auth_file_paths",
                return_value=[auth_path],
            ),
        ):
            result = read_auth_file_credentials("oci://ubuntu:latest")
            assert result.username == "quayuser"
            assert result.password.get_secret_value() == "quaypass"

    def test_no_match_in_any_registry(self, tmp_path):
        auth_path = tmp_path / "auth.json"
        auth_path.write_text(_make_auth_json({"ghcr.io": {"auth": _encode_auth("user", "pass")}}))

        with (
            patch(
                "jumpstarter.common.oci._get_unqualified_search_registries",
                return_value=["quay.io", "docker.io"],
            ),
            patch(
                "jumpstarter.common.oci._get_auth_file_paths",
                return_value=[auth_path],
            ),
        ):
            result = read_auth_file_credentials("oci://ubuntu:latest")
            assert result.username is None
            assert result.password is None


class TestResolveOciCredentialsExplicitArgs:
    """Verify resolve_oci_credentials with explicit username/password parameters."""

    def test_explicit_args_take_highest_priority(self, monkeypatch):
        monkeypatch.setenv("OCI_USERNAME", "env-user")
        monkeypatch.setenv("OCI_PASSWORD", "env-pass")

        result = resolve_oci_credentials("oci://quay.io/org/image:tag", username="explicit", password="creds")
        assert result.username == "explicit"
        assert result.plain_password == "creds"

    def test_partial_explicit_args_raises_value_error(self):
        with pytest.raises(ValueError, match="both username and password"):
            resolve_oci_credentials("oci://quay.io/org/image:tag", username="user", password=None)

        with pytest.raises(ValueError, match="both username and password"):
            resolve_oci_credentials("oci://quay.io/org/image:tag", username=None, password="pass")

    def test_empty_string_args_fall_through_to_env(self, monkeypatch):
        monkeypatch.setenv("OCI_USERNAME", "env-user")
        monkeypatch.setenv("OCI_PASSWORD", "env-pass")

        result = resolve_oci_credentials("oci://quay.io/org/image:tag", username="", password="")
        assert result.username == "env-user"
        assert result.plain_password == "env-pass"

    def test_none_args_fall_through_to_env(self, monkeypatch):
        monkeypatch.setenv("OCI_USERNAME", "env-user")
        monkeypatch.setenv("OCI_PASSWORD", "env-pass")

        result = resolve_oci_credentials("oci://quay.io/org/image:tag")
        assert result.username == "env-user"
        assert result.plain_password == "env-pass"
