from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import DhcpInfo
from jumpstarter.testing_strategies import ARBITRARY


class TestDhcpInfoRobustness:
    @given(ip_address=ARBITRARY, gateway=ARBITRARY, netmask=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, ip_address: object, gateway: object, netmask: object) -> None:
        try:
            DhcpInfo(ip_address=ip_address, gateway=gateway, netmask=netmask)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DhcpInfo constructor crashed: {type(exc).__name__}: {exc}") from exc

    @given(ip_address=st.text(max_size=50), gateway=st.text(max_size=50), netmask=st.text(max_size=50))
    def test_constructor_accepts_text(self, ip_address: str, gateway: str, netmask: str) -> None:
        try:
            info = DhcpInfo(ip_address=ip_address, gateway=gateway, netmask=netmask)
            assert isinstance(info.ip_address, str)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DhcpInfo constructor crashed: {type(exc).__name__}: {exc}") from exc

    @given(netmask=st.text(max_size=50))
    def test_cidr_property_never_crashes(self, netmask: str) -> None:
        try:
            info = DhcpInfo(ip_address="10.0.0.1", gateway="10.0.0.1", netmask=netmask)
            cidr = info.cidr
            assert isinstance(cidr, str)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DhcpInfo.cidr crashed: {type(exc).__name__}: {exc}") from exc
