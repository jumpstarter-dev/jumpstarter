from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .exporter import (
    ExporterConfigV1Alpha1DriverInstanceBase,
    HookConfigV1Alpha1,
    HookInstanceConfigV1Alpha1,
)
from .tls import TLSConfigV1Alpha1
from jumpstarter.testing_strategies import arbitrary as ARBITRARY


class TestTLSConfigRobustness:
    @given(ca=ARBITRARY, insecure=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, ca: object, insecure: object) -> None:
        try:
            cast(Any, TLSConfigV1Alpha1)(ca=ca, insecure=insecure)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"TLSConfigV1Alpha1 raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(ca=st.text(), insecure=st.booleans())
    def test_constructor_with_valid_types_never_crashes(self, ca: str, insecure: bool) -> None:
        try:
            config = TLSConfigV1Alpha1(ca=ca, insecure=insecure)
            assert isinstance(config.ca, str)
            assert isinstance(config.insecure, bool)
        except (ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"TLSConfigV1Alpha1 raised unexpected {type(exc).__name__}: {exc}") from exc


class TestHookInstanceConfigRobustness:
    @given(
        exec_val=st.one_of(st.text(), st.none()),
        script=ARBITRARY,
        timeout=ARBITRARY,
        on_failure=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(
        self,
        exec_val: str | None,
        script: object,
        timeout: object,
        on_failure: object,
    ) -> None:
        try:
            cast(Any, HookInstanceConfigV1Alpha1)(
                script=script,
                timeout=timeout,
                onFailure=on_failure,
            )
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"HookInstanceConfigV1Alpha1 raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(script=st.text(), timeout=st.integers(), on_failure=st.text())
    def test_constructor_with_typed_args_never_crashes(self, script: str, timeout: int, on_failure: str) -> None:
        try:
            cast(Any, HookInstanceConfigV1Alpha1)(
                script=script,
                timeout=timeout,
                onFailure=on_failure,
            )
        except (ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"HookInstanceConfigV1Alpha1 raised unexpected {type(exc).__name__}: {exc}") from exc


class TestHookConfigRobustness:
    @given(before_lease=ARBITRARY, after_lease=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, before_lease: object, after_lease: object) -> None:
        try:
            cast(Any, HookConfigV1Alpha1)(beforeLease=before_lease, afterLease=after_lease)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"HookConfigV1Alpha1 raised unexpected {type(exc).__name__}: {exc}") from exc


class TestDriverInstanceBaseRobustness:
    @given(
        type_val=st.text(),
        description=st.one_of(st.text(), st.none()),
        config=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=3),
    )
    def test_constructor_never_crashes_on_text(self, type_val: str, description: str | None, config: dict) -> None:
        try:
            cast(Any, ExporterConfigV1Alpha1DriverInstanceBase)(type=type_val, description=description, config=config)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(
                f"ExporterConfigV1Alpha1DriverInstanceBase raised unexpected {type(exc).__name__}: {exc}"
            ) from exc

    @given(
        type_val=ARBITRARY,
        description=ARBITRARY,
        config=ARBITRARY,
    )
    def test_constructor_never_crashes_on_arbitrary(
        self, type_val: object, description: object, config: object
    ) -> None:
        try:
            cast(Any, ExporterConfigV1Alpha1DriverInstanceBase)(type=type_val, description=description, config=config)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(
                f"ExporterConfigV1Alpha1DriverInstanceBase raised unexpected {type(exc).__name__}: {exc}"
            ) from exc
