import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from .oci import OciCredentials

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
