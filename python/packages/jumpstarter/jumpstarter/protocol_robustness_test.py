from typing import Any, cast

from google.protobuf.message import DecodeError
from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_protocol.jumpstarter.client.v1 import client_pb2
from jumpstarter_protocol.jumpstarter.v1 import (
    jumpstarter_pb2,
    kubernetes_pb2,
    router_pb2,
)

from jumpstarter.testing_strategies import arbitrary as ARBITRARY


class TestConditionRobustness:
    @given(
        type_val=ARBITRARY,
        status_val=ARBITRARY,
        reason=ARBITRARY,
        message=ARBITRARY,
        observed_generation=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(
        self,
        type_val: object,
        status_val: object,
        reason: object,
        message: object,
        observed_generation: object,
    ) -> None:
        try:
            cast(Any, kubernetes_pb2.Condition)(
                type=type_val,
                status=status_val,
                reason=reason,
                message=message,
                observedGeneration=observed_generation,
            )
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"Condition raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLabelSelectorRequirementRobustness:
    @given(
        key=ARBITRARY,
        operator=ARBITRARY,
        values=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(
        self,
        key: object,
        operator: object,
        values: object,
    ) -> None:
        try:
            cast(Any, kubernetes_pb2.LabelSelectorRequirement)(key=key, operator=operator, values=values)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"LabelSelectorRequirement raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLabelSelectorRobustness:
    @given(
        match_labels=st.one_of(
            st.none(),
            st.dictionaries(st.text(max_size=10), st.text(max_size=10), max_size=3),
            st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=3),
        ),
    )
    def test_constructor_never_crashes_on_dicts(self, match_labels: object) -> None:
        try:
            if match_labels is None:
                kubernetes_pb2.LabelSelector()
            else:
                cast(Any, kubernetes_pb2.LabelSelector)(match_labels=match_labels)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"LabelSelector raised unexpected {type(exc).__name__}: {exc}") from exc


class TestTimeRobustness:
    @given(seconds=ARBITRARY, nanos=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, seconds: object, nanos: object) -> None:
        try:
            cast(Any, kubernetes_pb2.Time)(seconds=seconds, nanos=nanos)
        except (TypeError, ValueError, OverflowError):
            pass
        except Exception as exc:
            raise AssertionError(f"Time raised unexpected {type(exc).__name__}: {exc}") from exc


class TestStreamRequestRobustness:
    @given(payload=ARBITRARY, frame_type=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, payload: object, frame_type: object) -> None:
        try:
            cast(Any, router_pb2.StreamRequest)(payload=payload, frame_type=frame_type)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"StreamRequest raised unexpected {type(exc).__name__}: {exc}") from exc


class TestRegisterRequestRobustness:
    @given(
        labels=st.one_of(
            st.none(),
            st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=3),
        ),
    )
    def test_constructor_never_crashes_on_dicts(self, labels: object) -> None:
        try:
            if labels is None:
                jumpstarter_pb2.RegisterRequest()
            else:
                cast(Any, jumpstarter_pb2.RegisterRequest)(labels=labels)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"RegisterRequest raised unexpected {type(exc).__name__}: {exc}") from exc


class TestDriverCallRequestRobustness:
    @given(uuid=ARBITRARY, method=ARBITRARY)
    def test_constructor_never_crashes_unexpectedly(self, uuid: object, method: object) -> None:
        try:
            cast(Any, jumpstarter_pb2.DriverCallRequest)(uuid=uuid, method=method)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"DriverCallRequest raised unexpected {type(exc).__name__}: {exc}") from exc


class TestExporterMessageRobustness:
    @given(
        name=ARBITRARY,
        status=ARBITRARY,
        status_message=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(
        self,
        name: object,
        status: object,
        status_message: object,
    ) -> None:
        try:
            cast(Any, client_pb2.Exporter)(name=name, status=status, status_message=status_message)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"Exporter raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLeaseMessageRobustness:
    @given(
        name=ARBITRARY,
        selector=ARBITRARY,
    )
    def test_constructor_never_crashes_unexpectedly(self, name: object, selector: object) -> None:
        try:
            cast(Any, client_pb2.Lease)(name=name, selector=selector)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"Lease raised unexpected {type(exc).__name__}: {exc}") from exc


class TestSerializeFromArbitraryBytesRobustness:
    @given(data=st.binary(max_size=200))
    def test_condition_parse_from_string_never_crashes(self, data: bytes) -> None:
        try:
            msg = kubernetes_pb2.Condition()
            msg.ParseFromString(data)
        except DecodeError:
            pass
        except Exception as exc:
            raise AssertionError(f"Condition.ParseFromString raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(data=st.binary(max_size=200))
    def test_label_selector_parse_from_string_never_crashes(self, data: bytes) -> None:
        try:
            msg = kubernetes_pb2.LabelSelector()
            msg.ParseFromString(data)
        except DecodeError:
            pass
        except Exception as exc:
            raise AssertionError(
                f"LabelSelector.ParseFromString raised unexpected {type(exc).__name__}: {exc}"
            ) from exc

    @given(data=st.binary(max_size=200))
    def test_stream_request_parse_from_string_never_crashes(self, data: bytes) -> None:
        try:
            msg = router_pb2.StreamRequest()
            msg.ParseFromString(data)
        except DecodeError:
            pass
        except Exception as exc:
            raise AssertionError(
                f"StreamRequest.ParseFromString raised unexpected {type(exc).__name__}: {exc}"
            ) from exc

    @given(data=st.binary(max_size=200))
    def test_register_request_parse_from_string_never_crashes(self, data: bytes) -> None:
        try:
            msg = jumpstarter_pb2.RegisterRequest()
            msg.ParseFromString(data)
        except DecodeError:
            pass
        except Exception as exc:
            raise AssertionError(
                f"RegisterRequest.ParseFromString raised unexpected {type(exc).__name__}: {exc}"
            ) from exc
