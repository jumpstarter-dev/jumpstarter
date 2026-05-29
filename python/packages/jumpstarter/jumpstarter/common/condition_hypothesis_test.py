from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_protocol import kubernetes_pb2

from .condition import condition_false, condition_message, condition_present_and_equal, condition_true

safe_text = st.text(min_size=1, max_size=30)


def _make_condition(
    condition_type: str,
    status: str,
    reason: str = "",
    message: str = "",
) -> kubernetes_pb2.Condition:
    return kubernetes_pb2.Condition(
        type=condition_type,
        status=status,
        reason=reason,
        message=message,
    )


class TestConditionTrue:
    @given(condition_type=safe_text)
    def test_returns_true_when_status_is_true(self, condition_type: str) -> None:
        conditions = [_make_condition(condition_type, "True")]
        assert condition_true(conditions, condition_type) is True

    @given(condition_type=safe_text)
    def test_returns_false_when_status_is_false(self, condition_type: str) -> None:
        conditions = [_make_condition(condition_type, "False")]
        assert condition_true(conditions, condition_type) is False

    @given(condition_type=safe_text)
    def test_returns_false_when_status_is_unknown(self, condition_type: str) -> None:
        conditions = [_make_condition(condition_type, "Unknown")]
        assert condition_true(conditions, condition_type) is False

    @given(condition_type=safe_text)
    def test_returns_false_when_condition_absent(self, condition_type: str) -> None:
        conditions = [_make_condition("OtherType", "True")]
        if condition_type != "OtherType":
            assert condition_true(conditions, condition_type) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert condition_true([], "Ready") is False


class TestConditionFalse:
    @given(condition_type=safe_text)
    def test_returns_true_when_status_is_false(self, condition_type: str) -> None:
        conditions = [_make_condition(condition_type, "False")]
        assert condition_false(conditions, condition_type) is True

    @given(condition_type=safe_text)
    def test_returns_false_when_status_is_true(self, condition_type: str) -> None:
        conditions = [_make_condition(condition_type, "True")]
        assert condition_false(conditions, condition_type) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert condition_false([], "Ready") is False


class TestConditionPresentAndEqual:
    @given(
        condition_type=safe_text,
        status=st.sampled_from(["True", "False", "Unknown"]),
    )
    def test_matches_type_and_status(self, condition_type: str, status: str) -> None:
        conditions = [_make_condition(condition_type, status)]
        assert condition_present_and_equal(conditions, condition_type, status) is True

    @given(
        condition_type=safe_text,
        reason=safe_text,
    )
    def test_matches_type_status_and_reason(self, condition_type: str, reason: str) -> None:
        conditions = [_make_condition(condition_type, "True", reason=reason)]
        assert condition_present_and_equal(conditions, condition_type, "True", reason=reason) is True

    @given(
        condition_type=safe_text,
        reason=safe_text,
        wrong_reason=safe_text,
    )
    def test_rejects_wrong_reason(self, condition_type: str, reason: str, wrong_reason: str) -> None:
        if reason != wrong_reason:
            conditions = [_make_condition(condition_type, "True", reason=reason)]
            assert condition_present_and_equal(conditions, condition_type, "True", reason=wrong_reason) is False

    @given(
        condition_type=safe_text,
        status=st.sampled_from(["True", "False", "Unknown"]),
        wrong_status=st.sampled_from(["True", "False", "Unknown"]),
    )
    def test_rejects_wrong_status(self, condition_type: str, status: str, wrong_status: str) -> None:
        if status != wrong_status:
            conditions = [_make_condition(condition_type, status)]
            assert condition_present_and_equal(conditions, condition_type, wrong_status) is False

    def test_returns_false_for_empty_list(self) -> None:
        assert condition_present_and_equal([], "Ready", "True") is False


class TestConditionMessage:
    @given(condition_type=safe_text, message=safe_text)
    def test_returns_message_when_present(self, condition_type: str, message: str) -> None:
        conditions = [_make_condition(condition_type, "True", message=message)]
        assert condition_message(conditions, condition_type) == message

    @given(condition_type=safe_text)
    def test_returns_none_when_absent(self, condition_type: str) -> None:
        conditions = [_make_condition("OtherType", "True", message="hello")]
        if condition_type != "OtherType":
            assert condition_message(conditions, condition_type) is None

    @given(
        condition_type=safe_text,
        reason=safe_text,
        message=safe_text,
    )
    def test_returns_message_with_matching_reason(self, condition_type: str, reason: str, message: str) -> None:
        conditions = [_make_condition(condition_type, "True", reason=reason, message=message)]
        assert condition_message(conditions, condition_type, reason=reason) == message

    @given(
        condition_type=safe_text,
        reason=safe_text,
        wrong_reason=safe_text,
    )
    def test_returns_none_with_wrong_reason(self, condition_type: str, reason: str, wrong_reason: str) -> None:
        if reason != wrong_reason:
            conditions = [_make_condition(condition_type, "True", reason=reason, message="msg")]
            assert condition_message(conditions, condition_type, reason=wrong_reason) is None

    def test_returns_none_for_empty_list(self) -> None:
        assert condition_message([], "Ready") is None


class TestMultipleConditions:
    @given(
        types=st.lists(safe_text, min_size=2, max_size=5, unique=True),
    )
    def test_finds_correct_condition_among_many(self, types: list[str]) -> None:
        target_type = types[0]
        conditions = [_make_condition(t, "False") for t in types]
        conditions[0] = _make_condition(target_type, "True")
        assert condition_true(conditions, target_type) is True
        for other_type in types[1:]:
            assert condition_true(conditions, other_type) is False
