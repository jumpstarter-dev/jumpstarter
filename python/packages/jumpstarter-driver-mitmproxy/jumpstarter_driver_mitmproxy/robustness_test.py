from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .driver import DirectoriesConfig, ListenConfig, WebConfig
from jumpstarter.testing_strategies import ARBITRARY


class TestListenConfigRobustness:
    @given(host=ARBITRARY, port=ARBITRARY)
    def test_constructor_never_crashes(self, host: object, port: object) -> None:
        try:
            cast(Any, ListenConfig)(host=host, port=port)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"ListenConfig crashed: {type(exc).__name__}: {exc}") from exc

    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_arbitrary_kwargs_never_crash(self, kwargs: dict) -> None:
        try:
            cast(Any, ListenConfig)(**kwargs)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"ListenConfig crashed: {type(exc).__name__}: {exc}") from exc


class TestWebConfigRobustness:
    @given(host=ARBITRARY, port=ARBITRARY)
    def test_constructor_never_crashes(self, host: object, port: object) -> None:
        try:
            cast(Any, WebConfig)(host=host, port=port)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"WebConfig crashed: {type(exc).__name__}: {exc}") from exc


class TestDirectoriesConfigRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            cast(Any, DirectoriesConfig)(**kwargs)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DirectoriesConfig crashed: {type(exc).__name__}: {exc}") from exc

    @given(data=ARBITRARY, conf=ARBITRARY, flows=ARBITRARY, addons=ARBITRARY, mocks=ARBITRARY, files=ARBITRARY)
    def test_all_fields_arbitrary(
        self,
        data: object,
        conf: object,
        flows: object,
        addons: object,
        mocks: object,
        files: object,
    ) -> None:
        try:
            cast(Any, DirectoriesConfig)(data=data, conf=conf, flows=flows, addons=addons, mocks=mocks, files=files)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DirectoriesConfig crashed: {type(exc).__name__}: {exc}") from exc
