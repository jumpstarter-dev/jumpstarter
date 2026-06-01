from typing import Any, cast

from hypothesis import given

from .driver import HttpAuthConfig, HttpBasicAuth, HttpEndpointConfig
from jumpstarter.testing_strategies import ARBITRARY


class TestHttpEndpointConfigRobustness:
    @given(url=ARBITRARY, method=ARBITRARY, data=ARBITRARY)
    def test_constructor_never_crashes(self, url: object, method: object, data: object) -> None:
        try:
            cast(Any, HttpEndpointConfig)(url=url, method=method, data=data)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpEndpointConfig crashed: {type(exc).__name__}: {exc}") from exc


class TestHttpBasicAuthRobustness:
    @given(user=ARBITRARY, password=ARBITRARY)
    def test_constructor_never_crashes(self, user: object, password: object) -> None:
        try:
            cast(Any, HttpBasicAuth)(user=user, password=password)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpBasicAuth crashed: {type(exc).__name__}: {exc}") from exc


class TestHttpAuthConfigRobustness:
    @given(basic=ARBITRARY)
    def test_constructor_never_crashes(self, basic: object) -> None:
        try:
            cast(Any, HttpAuthConfig)(basic=basic)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"HttpAuthConfig crashed: {type(exc).__name__}: {exc}") from exc
