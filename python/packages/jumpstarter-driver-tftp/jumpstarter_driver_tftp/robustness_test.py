import pytest

pytest.importorskip("jumpstarter_driver_tftp")

from hypothesis import given
from hypothesis import strategies as st

from .driver import Tftp, TftpError

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestTftpRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            Tftp(**kwargs)
        except (TypeError, ValueError, TftpError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"Tftp crashed: {type(exc).__name__}: {exc}") from exc
