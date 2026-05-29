import pytest

pytest.importorskip("jumpstarter_driver_probe_rs")

from hypothesis import given
from hypothesis import strategies as st

from .driver import ProbeRs

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestProbeRsRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            ProbeRs(**kwargs)
        except (TypeError, ValueError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"ProbeRs crashed: {type(exc).__name__}: {exc}") from exc
