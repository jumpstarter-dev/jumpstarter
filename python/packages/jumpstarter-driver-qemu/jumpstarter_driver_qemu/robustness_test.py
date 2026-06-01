from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .driver import Hostfwd
from jumpstarter.testing_strategies import ARBITRARY


class TestHostfwdRobustness:
    @given(protocol=ARBITRARY, hostaddr=ARBITRARY, hostport=ARBITRARY, guestport=ARBITRARY)
    def test_constructor_never_crashes(
        self, protocol: object, hostaddr: object, hostport: object, guestport: object
    ) -> None:
        try:
            Hostfwd(protocol=protocol, hostaddr=hostaddr, hostport=hostport, guestport=guestport)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Hostfwd crashed: {type(exc).__name__}: {exc}") from exc

    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_arbitrary_kwargs_never_crash(self, kwargs: dict) -> None:
        try:
            Hostfwd(**kwargs)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Hostfwd crashed: {type(exc).__name__}: {exc}") from exc

    @given(hostport=st.integers(), guestport=st.integers())
    def test_port_range_validation(self, hostport: int, guestport: int) -> None:
        try:
            Hostfwd(hostport=hostport, guestport=guestport)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Hostfwd crashed: {type(exc).__name__}: {exc}") from exc
