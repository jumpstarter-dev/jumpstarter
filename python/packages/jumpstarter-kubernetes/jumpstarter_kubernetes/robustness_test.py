from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .clients import V1Alpha1Client, V1Alpha1ClientStatus
from .datetime import time_since
from .exporters import V1Alpha1Exporter, V1Alpha1ExporterDevice, V1Alpha1ExporterStatus
from .leases import V1Alpha1Lease, V1Alpha1LeaseSelector

ARBITRARY = st.one_of(
    st.text(max_size=50),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
)

ARBITRARY_DICT = st.recursive(
    ARBITRARY,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=20), children, max_size=3),
    ),
    max_leaves=10,
)


class TestTimeSinceRobustness:
    @given(t_str=st.text())
    def test_time_since_never_crashes_unexpectedly_on_text(self, t_str: str) -> None:
        try:
            result = time_since(t_str)
            assert isinstance(result, str)
        except ValueError:
            pass
        except Exception as exc:
            raise AssertionError(f"time_since raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(t_str=ARBITRARY)
    def test_time_since_never_crashes_on_arbitrary(self, t_str: object) -> None:
        try:
            cast(Any, time_since)(t_str)
        except (TypeError, ValueError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"time_since raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1ExporterDeviceRobustness:
    @given(labels=ARBITRARY, uuid_val=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, labels: object, uuid_val: object) -> None:
        try:
            device = cast(Any, V1Alpha1ExporterDevice)(labels=labels, uuid=uuid_val)
        except (TypeError, ValueError, ValidationError):
            return
        except Exception as exc:
            raise AssertionError(f"V1Alpha1ExporterDevice raised unexpected {type(exc).__name__}: {exc}") from exc
        assert isinstance(device.labels, dict)


class TestV1Alpha1ExporterStatusRobustness:
    @given(
        credential=ARBITRARY,
        devices=ARBITRARY,
        endpoint=ARBITRARY,
        exporter_status=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(
        self,
        credential: object,
        devices: object,
        endpoint: object,
        exporter_status: object,
    ) -> None:
        try:
            cast(Any, V1Alpha1ExporterStatus)(
                credential=credential,
                devices=devices,
                endpoint=endpoint,
                exporterStatus=exporter_status,
            )
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1ExporterStatus raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1ExporterFromDictRobustness:
    @given(d=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_from_dict_never_crashes_unexpectedly(self, d: dict) -> None:
        try:
            V1Alpha1Exporter.from_dict(d)
        except (
            TypeError,
            ValueError,
            ValidationError,
            KeyError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1Exporter.from_dict raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1LeaseSelectorRobustness:
    @given(match_labels=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, match_labels: object) -> None:
        try:
            cast(Any, V1Alpha1LeaseSelector)(matchLabels=match_labels)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1LeaseSelector raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1LeaseFromDictRobustness:
    @given(d=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_from_dict_never_crashes_unexpectedly(self, d: dict) -> None:
        try:
            V1Alpha1Lease.from_dict(d)
        except (
            TypeError,
            ValueError,
            ValidationError,
            KeyError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1Lease.from_dict raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1ClientFromDictRobustness:
    @given(d=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_from_dict_never_crashes_unexpectedly(self, d: dict) -> None:
        try:
            V1Alpha1Client.from_dict(d)
        except (
            TypeError,
            ValueError,
            ValidationError,
            KeyError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1Client.from_dict raised unexpected {type(exc).__name__}: {exc}") from exc


class TestV1Alpha1ClientStatusRobustness:
    @given(credential=ARBITRARY, endpoint=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, credential: object, endpoint: object) -> None:
        try:
            cast(Any, V1Alpha1ClientStatus)(credential=credential, endpoint=endpoint)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"V1Alpha1ClientStatus raised unexpected {type(exc).__name__}: {exc}") from exc
