from hypothesis import given
from hypothesis import strategies as st

from .driver import HttpAuthConfig, HttpBasicAuth, HttpEndpointConfig

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestHttpEndpointConfigRobustness:
    @given(url=ARBITRARY, method=ARBITRARY, data=ARBITRARY)
    def test_constructor_never_crashes(self, url: object, method: object, data: object) -> None:
        try:
            HttpEndpointConfig(url=url, method=method, data=data)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpEndpointConfig crashed: {type(exc).__name__}: {exc}") from exc


class TestHttpBasicAuthRobustness:
    @given(user=ARBITRARY, password=ARBITRARY)
    def test_constructor_never_crashes(self, user: object, password: object) -> None:
        try:
            HttpBasicAuth(user=user, password=password)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpBasicAuth crashed: {type(exc).__name__}: {exc}") from exc


class TestHttpAuthConfigRobustness:
    @given(basic=ARBITRARY)
    def test_constructor_never_crashes(self, basic: object) -> None:
        try:
            HttpAuthConfig(basic=basic)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpAuthConfig crashed: {type(exc).__name__}: {exc}") from exc
