from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_protocol import kubernetes_pb2

from .condition import (
    condition_false,
    condition_message,
    condition_present_and_equal,
    condition_true,
)


def arbitrary_condition_list():
    return st.lists(
        st.builds(
            kubernetes_pb2.Condition,
            type=st.text(max_size=20),
            status=st.text(max_size=10),
            reason=st.text(max_size=20),
            message=st.text(max_size=50),
        ),
        max_size=5,
    )


class TestConditionPresentAndEqualRobustness:
    @given(
        conditions=arbitrary_condition_list(),
        condition_type=st.text(),
        status=st.text(),
        reason=st.one_of(st.text(), st.none()),
    )
    def test_never_crashes_on_arbitrary_text(
        self,
        conditions: list,
        condition_type: str,
        status: str,
        reason: str | None,
    ) -> None:
        try:
            result = condition_present_and_equal(conditions, condition_type, status, reason)
            assert isinstance(result, bool)
        except Exception as exc:
            raise AssertionError(f"condition_present_and_equal raised unexpected {type(exc).__name__}: {exc}") from exc


class TestConditionTrueRobustness:
    @given(
        conditions=arbitrary_condition_list(),
        condition_type=st.text(),
    )
    def test_never_crashes_on_text(self, conditions: list, condition_type: str) -> None:
        try:
            result = condition_true(conditions, condition_type)
            assert isinstance(result, bool)
        except Exception as exc:
            raise AssertionError(f"condition_true raised unexpected {type(exc).__name__}: {exc}") from exc


class TestConditionFalseRobustness:
    @given(
        conditions=arbitrary_condition_list(),
        condition_type=st.text(),
    )
    def test_never_crashes_on_text(self, conditions: list, condition_type: str) -> None:
        try:
            result = condition_false(conditions, condition_type)
            assert isinstance(result, bool)
        except Exception as exc:
            raise AssertionError(f"condition_false raised unexpected {type(exc).__name__}: {exc}") from exc


class TestConditionMessageRobustness:
    @given(
        conditions=arbitrary_condition_list(),
        condition_type=st.text(),
        reason=st.one_of(st.text(), st.none()),
    )
    def test_never_crashes_on_text(self, conditions: list, condition_type: str, reason: str | None) -> None:
        try:
            result = condition_message(conditions, condition_type, reason)
            assert result is None or isinstance(result, str)
        except Exception as exc:
            raise AssertionError(f"condition_message raised unexpected {type(exc).__name__}: {exc}") from exc
