from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import (
    SomeIpEventNotification,
    SomeIpMessageResponse,
    SomeIpPayload,
    SomeIpServiceEntry,
)
from jumpstarter.testing_strategies import ARBITRARY


class TestSomeIpPayloadRobustness:
    @given(data=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, data: object) -> None:
        try:
            cast(Any, SomeIpPayload)(data=data)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"SomeIpPayload constructor crashed: {type(exc).__name__}: {exc}") from exc

    @given(data=st.text(max_size=200))
    def test_constructor_never_crashes_on_text(self, data: str) -> None:
        try:
            cast(Any, SomeIpPayload)(data=data)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"SomeIpPayload constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestSomeIpMessageResponseRobustness:
    @given(
        service_id=ARBITRARY,
        method_id=ARBITRARY,
        client_id=ARBITRARY,
        session_id=ARBITRARY,
        message_type=ARBITRARY,
        return_code=ARBITRARY,
        payload=ARBITRARY,
    )
    def test_constructor_never_crashes_on_arbitrary(
        self,
        service_id: object,
        method_id: object,
        client_id: object,
        session_id: object,
        message_type: object,
        return_code: object,
        payload: object,
    ) -> None:
        try:
            cast(Any, SomeIpMessageResponse)(
                service_id=service_id,
                method_id=method_id,
                client_id=client_id,
                session_id=session_id,
                message_type=message_type,
                return_code=return_code,
                payload=payload,
            )
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"SomeIpMessageResponse constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestSomeIpServiceEntryRobustness:
    @given(service_id=ARBITRARY, instance_id=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, service_id: object, instance_id: object) -> None:
        try:
            cast(Any, SomeIpServiceEntry)(service_id=service_id, instance_id=instance_id)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"SomeIpServiceEntry constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestSomeIpEventNotificationRobustness:
    @given(service_id=ARBITRARY, event_id=ARBITRARY, payload=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(
        self, service_id: object, event_id: object, payload: object
    ) -> None:
        try:
            cast(Any, SomeIpEventNotification)(service_id=service_id, event_id=event_id, payload=payload)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"SomeIpEventNotification constructor crashed: {type(exc).__name__}: {exc}") from exc
