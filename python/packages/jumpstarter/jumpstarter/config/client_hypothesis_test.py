import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .client import ClientConfigV1Alpha1Drivers, ClientConfigV1Alpha1Lease


class TestClientConfigLease:
    @given(timeout=st.integers(min_value=5, max_value=100000))
    def test_valid_acquisition_timeout(self, timeout: int) -> None:
        lease = ClientConfigV1Alpha1Lease(acquisition_timeout=timeout)
        assert lease.acquisition_timeout == timeout

    def test_default_acquisition_timeout(self) -> None:
        lease = ClientConfigV1Alpha1Lease()
        assert lease.acquisition_timeout == 7200

    @given(timeout=st.integers(min_value=-1000, max_value=4))
    def test_below_minimum_acquisition_timeout_rejected(self, timeout: int) -> None:
        with pytest.raises(ValidationError):
            ClientConfigV1Alpha1Lease(acquisition_timeout=timeout)

    @given(dial_timeout=st.floats(min_value=0.001, max_value=3600.0, allow_nan=False, allow_infinity=False))
    def test_valid_dial_timeout(self, dial_timeout: float) -> None:
        lease = ClientConfigV1Alpha1Lease(dial_timeout=dial_timeout)
        assert lease.dial_timeout == dial_timeout

    def test_zero_dial_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClientConfigV1Alpha1Lease(dial_timeout=0)

    def test_negative_dial_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClientConfigV1Alpha1Lease(dial_timeout=-1.0)


class TestClientConfigDrivers:
    @given(
        allow_list=st.lists(st.text(min_size=1, max_size=50), max_size=10),
    )
    def test_allow_list_construction(self, allow_list: list[str]) -> None:
        drivers = ClientConfigV1Alpha1Drivers(allow=allow_list)
        assert drivers.allow == allow_list

    def test_empty_allow_list_default(self) -> None:
        drivers = ClientConfigV1Alpha1Drivers()
        assert drivers.allow == []
        assert drivers.unsafe is False

    def test_unsafe_flag_from_allow_list(self) -> None:
        drivers = ClientConfigV1Alpha1Drivers(allow=["some-driver", "UNSAFE"])
        assert drivers.unsafe is True

    @given(
        csv=st.text(
            alphabet=st.characters(categories=("L", "N", "P")),
            min_size=1,
            max_size=100,
        ),
    )
    def test_csv_string_decoded(self, csv: str) -> None:
        drivers = ClientConfigV1Alpha1Drivers(allow=csv)
        assert isinstance(drivers.allow, list)
        assert drivers.allow == csv.split(",")

    @given(
        grpc_key=st.from_regex(r"grpc\.[a-z_]{1,20}", fullmatch=True),
        grpc_val=st.one_of(
            st.text(min_size=1, max_size=20),
            st.integers(min_value=0, max_value=100000),
        ),
    )
    def test_grpc_options_accept_various_types(self, grpc_key: str, grpc_val: str | int) -> None:
        from .client import ClientConfigV1Alpha1
        from .common import ObjectMeta

        config = ClientConfigV1Alpha1(
            metadata=ObjectMeta(namespace="default", name="test"),
            grpcOptions={grpc_key: grpc_val},
        )
        assert config.grpcOptions is not None
        assert config.grpcOptions[grpc_key] == grpc_val
