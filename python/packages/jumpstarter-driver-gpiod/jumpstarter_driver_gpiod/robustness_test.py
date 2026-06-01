from typing import Any, cast

import pytest

pytest.importorskip("jumpstarter_driver_gpiod")

from hypothesis import given
from hypothesis import strategies as st

from .driver import DigitalInput, DigitalOutput
from jumpstarter.testing_strategies import ARBITRARY


class TestDigitalOutputRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            cast(Any, DigitalOutput)(**kwargs)
        except (TypeError, ValueError, ImportError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"DigitalOutput crashed: {type(exc).__name__}: {exc}") from exc


class TestDigitalInputRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            cast(Any, DigitalInput)(**kwargs)
        except (TypeError, ValueError, ImportError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"DigitalInput crashed: {type(exc).__name__}: {exc}") from exc
