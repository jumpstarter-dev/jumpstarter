import pytest

pytest.importorskip("jumpstarter_driver_shell")

from hypothesis import given
from hypothesis import strategies as st

from .driver import Shell

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestShellRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            Shell(**kwargs)
        except (TypeError, ValueError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"Shell crashed: {type(exc).__name__}: {exc}") from exc
