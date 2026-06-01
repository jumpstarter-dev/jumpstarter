import pytest

pytest.importorskip("jumpstarter_driver_noyito_relay")

from hypothesis import given
from hypothesis import strategies as st

from .driver import NoyitoPowerHID, NoyitoPowerSerial
from jumpstarter.testing_strategies import ARBITRARY


class TestNoyitoPowerSerialRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            NoyitoPowerSerial(**kwargs)
        except (TypeError, ValueError, OSError, RuntimeError, NotImplementedError):
            pass
        except Exception as exc:
            raise AssertionError(f"NoyitoPowerSerial crashed: {type(exc).__name__}: {exc}") from exc


class TestNoyitoPowerHIDRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            NoyitoPowerHID(**kwargs)
        except (TypeError, ValueError, OSError, RuntimeError, NotImplementedError):
            pass
        except Exception as exc:
            raise AssertionError(f"NoyitoPowerHID crashed: {type(exc).__name__}: {exc}") from exc
