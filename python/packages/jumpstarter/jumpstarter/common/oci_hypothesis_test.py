import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from .oci import OciCredentials, parse_oci_registry

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
    @settings(max_examples=50)
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
    @settings(max_examples=50)
    def test_only_username_raises(self, username: str) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(username=username)

    @given(password=non_empty_stripped_text)
    @settings(max_examples=50)
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
    @settings(max_examples=30)
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
    @settings(max_examples=30)
    def test_whitespace_only_password_becomes_none(self, padding: str) -> None:
        creds = OciCredentials(password=padding)
        assert creds.plain_password is None
        assert creds.is_authenticated is False


class TestOciCredentialsRoundtrip:
    @given(
        username=non_empty_stripped_text,
        password=non_empty_stripped_text,
    )
    @settings(max_examples=50)
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
    @settings(max_examples=50)
    def test_frozen_model_is_hashable(self, username: str, password: str) -> None:
        creds_a = OciCredentials(username=username, password=password)
        creds_b = OciCredentials(username=username, password=password)
        assert hash(creds_a) == hash(creds_b)


registry_host: st.SearchStrategy[str] = st.from_regex(
    r"[a-z][a-z0-9-]{0,20}\.[a-z]{2,6}", fullmatch=True
)
port: st.SearchStrategy[int] = st.integers(min_value=1, max_value=65535)
image_name: st.SearchStrategy[str] = st.from_regex(
    r"[a-z][a-z0-9/-]{0,30}", fullmatch=True
)
tag: st.SearchStrategy[str] = st.from_regex(r"[a-zA-Z0-9._-]{1,20}", fullmatch=True)


class TestParseOciRegistry:
    @given(host=registry_host, img=image_name, t=tag)
    @settings(max_examples=50)
    def test_explicit_registry_extracted(self, host: str, img: str, t: str) -> None:
        url = f"{host}/{img}:{t}"
        assert parse_oci_registry(url) == host

    @given(host=registry_host, img=image_name, t=tag)
    @settings(max_examples=50)
    def test_oci_scheme_stripped(self, host: str, img: str, t: str) -> None:
        url = f"oci://{host}/{img}:{t}"
        assert parse_oci_registry(url) == host

    @given(host=registry_host, p=port, img=image_name)
    @settings(max_examples=50)
    def test_registry_with_port_preserved(self, host: str, p: int, img: str) -> None:
        url = f"{host}:{p}/{img}"
        assert parse_oci_registry(url) == f"{host}:{p}"

    @given(host=registry_host, img=image_name)
    @settings(max_examples=30)
    def test_idempotent_with_and_without_oci_prefix(self, host: str, img: str) -> None:
        plain = f"{host}/{img}"
        prefixed = f"oci://{host}/{img}"
        assert parse_oci_registry(plain) == parse_oci_registry(prefixed)
