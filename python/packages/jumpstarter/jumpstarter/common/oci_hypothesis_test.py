import base64
import json
from pathlib import Path
from unittest import mock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .oci import OciCredentials, parse_oci_registry, read_auth_file_credentials, resolve_oci_credentials

non_empty_stripped_text: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=100,
).filter(lambda s: s.strip() != "")


class TestOciCredentialsBothOrNeither:
    @given(
        username=non_empty_stripped_text,
        password=non_empty_stripped_text,
    )
    def test_both_set_is_authenticated(self, username: str, password: str) -> None:
        creds = OciCredentials(username=username, password=password)
        assert creds.is_authenticated is True
        assert creds.username == username.strip()
        assert creds.plain_password == password.strip()

    def test_neither_set_is_not_authenticated(self) -> None:
        creds = OciCredentials()
        assert creds.is_authenticated is False
        assert creds.username is None
        assert creds.plain_password is None

    @given(username=non_empty_stripped_text)
    def test_only_username_raises(self, username: str) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(username=username)

    @given(password=non_empty_stripped_text)
    def test_only_password_raises(self, password: str) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(password=password)


class TestOciCredentialsWhitespaceNormalization:
    @given(
        padding=st.text(
            alphabet=st.sampled_from([" ", "\t", "\n"]),
            min_size=1,
            max_size=10,
        ),
    )
    def test_whitespace_only_username_becomes_none(self, padding: str) -> None:
        creds = OciCredentials(username=padding)
        assert creds.username is None
        assert creds.is_authenticated is False

    @given(
        padding=st.text(
            alphabet=st.sampled_from([" ", "\t", "\n"]),
            min_size=1,
            max_size=10,
        ),
    )
    def test_whitespace_only_password_becomes_none(self, padding: str) -> None:
        creds = OciCredentials(password=padding)
        assert creds.plain_password is None
        assert creds.is_authenticated is False


class TestOciCredentialsRoundtrip:
    @given(
        username=non_empty_stripped_text,
        password=non_empty_stripped_text,
    )
    def test_roundtrip_through_dict(self, username: str, password: str) -> None:
        original = OciCredentials(username=username, password=password)
        dumped = original.model_dump()
        restored = OciCredentials.model_validate(dumped)
        assert restored.username == original.username
        assert restored.plain_password == original.plain_password

    @given(
        username=non_empty_stripped_text,
        password=non_empty_stripped_text,
    )
    def test_frozen_model_is_hashable(self, username: str, password: str) -> None:
        creds_a = OciCredentials(username=username, password=password)
        creds_b = OciCredentials(username=username, password=password)
        assert hash(creds_a) == hash(creds_b)


registry_host: st.SearchStrategy[str] = st.from_regex(r"[a-z][a-z0-9-]{0,20}\.[a-z]{2,6}", fullmatch=True)
port: st.SearchStrategy[int] = st.integers(min_value=1, max_value=65535)
image_name: st.SearchStrategy[str] = st.from_regex(r"[a-z][a-z0-9/-]{0,30}", fullmatch=True)
tag: st.SearchStrategy[str] = st.from_regex(r"[a-zA-Z0-9._-]{1,20}", fullmatch=True)


class TestParseOciRegistry:
    @given(host=registry_host, img=image_name, t=tag)
    def test_explicit_registry_extracted(self, host: str, img: str, t: str) -> None:
        url = f"{host}/{img}:{t}"
        assert parse_oci_registry(url) == host

    @given(host=registry_host, img=image_name, t=tag)
    def test_oci_scheme_stripped(self, host: str, img: str, t: str) -> None:
        url = f"oci://{host}/{img}:{t}"
        assert parse_oci_registry(url) == host

    @given(host=registry_host, p=port, img=image_name)
    def test_registry_with_port_preserved(self, host: str, p: int, img: str) -> None:
        url = f"{host}:{p}/{img}"
        assert parse_oci_registry(url) == f"{host}:{p}"

    @given(host=registry_host, img=image_name)
    def test_idempotent_with_and_without_oci_prefix(self, host: str, img: str) -> None:
        plain = f"{host}/{img}"
        prefixed = f"oci://{host}/{img}"
        assert parse_oci_registry(plain) == parse_oci_registry(prefixed)


def _write_auth_file(path: Path, registry: str, username: str, password: str) -> None:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    auth_data = {"auths": {registry: {"auth": encoded}}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(auth_data), encoding="utf-8")


class TestReadAuthFileCredentials:
    def test_returns_unauthenticated_when_no_files_exist(self, tmp_path: Path) -> None:
        fake_auth = tmp_path / "nonexistent" / "auth.json"
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[fake_auth]):
            creds = read_auth_file_credentials("quay.io/org/image:latest")
        assert creds.is_authenticated is False

    def test_reads_credentials_from_auth_file(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        _write_auth_file(auth_file, "quay.io", "testuser", "testpass")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_file]):
            creds = read_auth_file_credentials("quay.io/org/image:latest")
        assert creds.is_authenticated is True
        assert creds.username == "testuser"
        assert creds.plain_password == "testpass"

    def test_skips_malformed_auth_file(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json", encoding="utf-8")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[bad_file]):
            creds = read_auth_file_credentials("quay.io/org/image:latest")
        assert creds.is_authenticated is False

    def test_registry_mismatch_returns_unauthenticated(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        _write_auth_file(auth_file, "other.io", "user", "pass")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_file]):
            creds = read_auth_file_credentials("quay.io/org/image:latest")
        assert creds.is_authenticated is False


class TestResolveOciCredentials:
    def test_explicit_credentials_take_priority(self, tmp_path: Path) -> None:
        auth_file = tmp_path / "auth.json"
        _write_auth_file(auth_file, "quay.io", "fileuser", "filepass")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_file]):
            creds = resolve_oci_credentials("quay.io/org/image", username="explicit", password="secret")
        assert creds.username == "explicit"
        assert creds.plain_password == "secret"

    def test_env_vars_take_second_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        auth_file = tmp_path / "auth.json"
        _write_auth_file(auth_file, "quay.io", "fileuser", "filepass")
        monkeypatch.setenv("OCI_USERNAME", "envuser")
        monkeypatch.setenv("OCI_PASSWORD", "envpass")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_file]):
            creds = resolve_oci_credentials("quay.io/org/image")
        assert creds.username == "envuser"
        assert creds.plain_password == "envpass"

    def test_falls_back_to_auth_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCI_USERNAME", raising=False)
        monkeypatch.delenv("OCI_PASSWORD", raising=False)
        auth_file = tmp_path / "auth.json"
        _write_auth_file(auth_file, "quay.io", "fileuser", "filepass")
        with mock.patch("jumpstarter.common.oci._get_auth_file_paths", return_value=[auth_file]):
            creds = resolve_oci_credentials("quay.io/org/image")
        assert creds.username == "fileuser"
        assert creds.plain_password == "filepass"

    def test_raises_on_partial_explicit_credentials(self) -> None:
        with pytest.raises(ValueError, match="both username and password"):
            resolve_oci_credentials("quay.io/org/image", username="user")
