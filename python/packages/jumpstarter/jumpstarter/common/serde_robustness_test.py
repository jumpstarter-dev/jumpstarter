from hypothesis import given
from hypothesis import strategies as st

from .serde import encode_value

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.none(),
    st.booleans(),
)

NESTED = st.recursive(
    ARBITRARY,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=10), children, max_size=3),
    ),
    max_leaves=10,
)


class TestEncodeValueRobustness:
    @given(value=ARBITRARY)
    def test_encode_value_never_crashes_on_primitives(self, value: object) -> None:
        try:
            result = encode_value(value)
            assert result is not None
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"encode_value raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=NESTED)
    def test_encode_value_never_crashes_on_nested(self, value: object) -> None:
        try:
            encode_value(value)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"encode_value raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=st.binary())
    def test_encode_value_never_crashes_on_binary(self, value: bytes) -> None:
        try:
            encode_value(value)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"encode_value raised unexpected {type(exc).__name__}: {exc}") from exc
