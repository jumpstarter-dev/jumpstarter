from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st

from .driver import FilterConfig, FilterDirection, FilterRule
from jumpstarter.testing_strategies import ARBITRARY


class TestFilterRuleRobustness:
    @given(action=ARBITRARY, destination=ARBITRARY, source=ARBITRARY, port=ARBITRARY, protocol=ARBITRARY)
    def test_constructor_never_crashes(
        self,
        action: object,
        destination: object,
        source: object,
        port: object,
        protocol: object,
    ) -> None:
        try:
            cast(Any, FilterRule)(
                action=action,
                destination=destination,
                source=source,
                port=port,
                protocol=protocol,
            )
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"FilterRule crashed: {type(exc).__name__}: {exc}") from exc

    @given(action=st.sampled_from(["accept", "drop"]), port=ARBITRARY, protocol=ARBITRARY)
    def test_valid_action_with_arbitrary_port_protocol(self, action: str, port: object, protocol: object) -> None:
        try:
            cast(Any, FilterRule)(action=action, port=port, protocol=protocol)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"FilterRule crashed: {type(exc).__name__}: {exc}") from exc


class TestFilterDirectionRobustness:
    @given(policy=ARBITRARY, rules=ARBITRARY)
    def test_constructor_never_crashes(self, policy: object, rules: object) -> None:
        try:
            cast(Any, FilterDirection)(policy=policy, rules=rules)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"FilterDirection crashed: {type(exc).__name__}: {exc}") from exc


class TestFilterConfigRobustness:
    @given(egress=ARBITRARY, ingress=ARBITRARY)
    def test_constructor_never_crashes(self, egress: object, ingress: object) -> None:
        try:
            cast(Any, FilterConfig)(egress=egress, ingress=ingress)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"FilterConfig crashed: {type(exc).__name__}: {exc}") from exc

    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_from_dict_never_crashes(self, kwargs: dict) -> None:
        try:
            FilterConfig.from_dict(kwargs)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"FilterConfig.from_dict crashed: {type(exc).__name__}: {exc}") from exc
