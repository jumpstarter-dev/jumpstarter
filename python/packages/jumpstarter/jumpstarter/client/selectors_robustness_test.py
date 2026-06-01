import pytest
from hypothesis import given
from hypothesis import strategies as st

from .selectors import (
    _label_satisfies_expression,
    extract_match_labels_filter,
    parse_label_selector,
    selector_contains,
)
from jumpstarter.testing_strategies import arbitrary as ARBITRARY


class TestParseLabelSelectorRobustness:
    @given(arbitrary_input=st.text())
    def test_parse_label_selector_never_crashes_on_text(self, arbitrary_input: str) -> None:
        try:
            result = parse_label_selector(arbitrary_input)
        except Exception as exc:
            raise AssertionError(f"parse_label_selector raised unexpected {type(exc).__name__}: {exc}") from exc
        assert isinstance(result, tuple)
        assert len(result) == 2
        labels, exprs = result
        assert isinstance(labels, dict)
        assert isinstance(exprs, list)
        for key, value in labels.items():
            assert isinstance(key, str)
            assert isinstance(value, str)
        for expr in exprs:
            assert isinstance(expr, tuple)
            assert len(expr) == 3

    @given(arbitrary_input=st.binary())
    def test_parse_label_selector_never_crashes_on_binary(self, arbitrary_input: bytes) -> None:
        try:
            parse_label_selector(arbitrary_input)
        except TypeError:
            pass
        except Exception as exc:
            raise AssertionError(f"parse_label_selector raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(arbitrary_input=ARBITRARY)
    def test_parse_label_selector_never_crashes_on_arbitrary(self, arbitrary_input: object) -> None:
        try:
            parse_label_selector(arbitrary_input)
        except (TypeError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"parse_label_selector raised unexpected {type(exc).__name__}: {exc}") from exc


class TestSelectorContainsRobustness:
    @given(selector=st.text(), requirements=st.text())
    def test_selector_contains_never_crashes_on_text(self, selector: str, requirements: str) -> None:
        try:
            result = selector_contains(selector, requirements)
            assert isinstance(result, bool)
        except Exception as exc:
            raise AssertionError(f"selector_contains raised unexpected {type(exc).__name__}: {exc}") from exc


class TestExtractMatchLabelsFilterRobustness:
    @given(arbitrary_input=st.one_of(st.text(), st.none()))
    def test_extract_match_labels_filter_never_crashes(self, arbitrary_input: str | None) -> None:
        try:
            result = extract_match_labels_filter(arbitrary_input)
            assert result is None or isinstance(result, str)
        except Exception as exc:
            raise AssertionError(f"extract_match_labels_filter raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(arbitrary_input=ARBITRARY)
    def test_extract_match_labels_filter_never_crashes_on_arbitrary(self, arbitrary_input: object) -> None:
        try:
            extract_match_labels_filter(arbitrary_input)
        except (TypeError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"extract_match_labels_filter raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLabelSatisfiesExpressionRobustness:
    @given(
        labels=st.dictionaries(st.text(), st.text(), max_size=5),
        key=st.text(),
        operator=st.text(),
        values=st.lists(st.text(), max_size=5),
    )
    def test_label_satisfies_expression_never_crashes(
        self,
        labels: dict[str, str],
        key: str,
        operator: str,
        values: list[str],
    ) -> None:
        try:
            result = _label_satisfies_expression(labels, [], key, operator, values)
            assert isinstance(result, bool)
        except ValueError:
            pass
        except Exception as exc:
            raise AssertionError(f"_label_satisfies_expression raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLabelSatisfiesExpressionNegative:
    @given(
        operator=st.text().filter(lambda s: s not in ("in", "notin", "exists", "!exists", "!=")),
    )
    def test_unknown_operator_raises_value_error(self, operator: str) -> None:
        labels = {"key": "value"}
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression(labels, [], "key", operator, ["value"])

    def test_binary_input_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_label_selector(b"key=value")

    def test_integer_input_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError):
            parse_label_selector(42)
