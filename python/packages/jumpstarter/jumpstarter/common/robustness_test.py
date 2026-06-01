import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .enums import ExporterStatus, LogSource
from .metadata import Metadata
from .oci import OciCredentials, parse_oci_registry
from jumpstarter.testing_strategies import arbitrary as ARBITRARY

ALLOWED_PARSE_OCI_EXCEPTIONS = (TypeError, ValueError)


class TestMetadataRobustness:
    @given(
        uuid_val=ARBITRARY,
        labels=ARBITRARY,
    )
    def test_metadata_constructor_never_crashes_unexpectedly(self, uuid_val: object, labels: object) -> None:
        try:
            meta = Metadata(uuid=uuid_val, labels=labels)
        except (TypeError, ValueError):
            return
        except Exception as exc:
            raise AssertionError(f"Metadata raised unexpected {type(exc).__name__}: {exc}") from exc
        assert isinstance(meta.labels, dict)

    @given(labels=st.dictionaries(st.text(), ARBITRARY, max_size=5))
    def test_metadata_with_dict_labels_never_crashes(self, labels: dict[str, object]) -> None:
        try:
            meta = Metadata(labels=labels)
        except (TypeError, ValueError):
            return
        except Exception as exc:
            raise AssertionError(f"Metadata raised unexpected {type(exc).__name__}: {exc}") from exc
        assert isinstance(meta.labels, dict)


class TestExporterStatusFromProtoRobustness:
    @given(value=st.integers())
    def test_from_proto_never_crashes_on_integers(self, value: int) -> None:
        try:
            result = ExporterStatus.from_proto(value)
            assert isinstance(result, ExporterStatus)
        except ValueError:
            pass
        except Exception as exc:
            raise AssertionError(f"ExporterStatus.from_proto raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=ARBITRARY)
    def test_from_proto_never_crashes_on_arbitrary(self, value: object) -> None:
        try:
            ExporterStatus.from_proto(value)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"ExporterStatus.from_proto raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLogSourceFromProtoRobustness:
    @given(value=st.integers())
    def test_from_proto_never_crashes_on_integers(self, value: int) -> None:
        try:
            result = LogSource.from_proto(value)
            assert isinstance(result, LogSource)
        except ValueError:
            pass
        except Exception as exc:
            raise AssertionError(f"LogSource.from_proto raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=ARBITRARY)
    def test_from_proto_never_crashes_on_arbitrary(self, value: object) -> None:
        try:
            LogSource.from_proto(value)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"LogSource.from_proto raised unexpected {type(exc).__name__}: {exc}") from exc


class TestOciCredentialsRobustness:
    @given(username=ARBITRARY, password=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, username: object, password: object) -> None:
        try:
            creds = OciCredentials(username=username, password=password)
        except (TypeError, ValueError, ValidationError):
            return
        except Exception as exc:
            raise AssertionError(f"OciCredentials raised unexpected {type(exc).__name__}: {exc}") from exc
        assert isinstance(creds.is_authenticated, bool)

    @given(username=st.text(), password=st.text())
    def test_constructor_with_text_never_crashes(self, username: str, password: str) -> None:
        try:
            creds = OciCredentials(username=username, password=password)
            assert isinstance(creds.is_authenticated, bool)
        except ValueError:
            pass
        except Exception as exc:
            raise AssertionError(f"OciCredentials raised unexpected {type(exc).__name__}: {exc}") from exc


class TestParseOciRegistryRobustness:
    @given(oci_url=st.text())
    def test_parse_oci_registry_never_crashes_on_text(self, oci_url: str) -> None:
        try:
            result = parse_oci_registry(oci_url)
            assert isinstance(result, str)
        except ALLOWED_PARSE_OCI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"parse_oci_registry raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(oci_url=ARBITRARY)
    def test_parse_oci_registry_never_crashes_on_arbitrary(self, oci_url: object) -> None:
        try:
            parse_oci_registry(oci_url)
        except (TypeError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"parse_oci_registry raised unexpected {type(exc).__name__}: {exc}") from exc


class TestOciCredentialsNegative:
    @given(username=st.text(min_size=1).filter(lambda s: s.strip() != ""))
    def test_only_username_raises_validation_error(self, username: str) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(username=username)

    @given(password=st.text(min_size=1).filter(lambda s: s.strip() != ""))
    def test_only_password_raises_validation_error(self, password: str) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(password=password)

    def test_integer_username_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(username=42, password="pass")

    def test_integer_password_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            OciCredentials(username="user", password=42)
