"""Tests for label selector matching."""

import pytest

from jumpstarter.client.selectors import _label_satisfies_expression, selector_contains


class TestLabelSatisfiesExpressionIn:
    def test_value_present_in_list(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "in", ["a", "b"]) is True

    def test_value_absent_from_list(self) -> None:
        assert _label_satisfies_expression({"k": "c"}, [], "k", "in", ["a", "b"]) is False

    def test_key_missing(self) -> None:
        assert _label_satisfies_expression({}, [], "k", "in", ["a"]) is False

    def test_empty_values_list(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "in", []) is False

    def test_key_with_special_characters(self) -> None:
        assert _label_satisfies_expression({"a/b.c_d-e": "v"}, [], "a/b.c_d-e", "in", ["v"]) is True


class TestLabelSatisfiesExpressionExists:
    def test_key_present_in_labels(self) -> None:
        assert _label_satisfies_expression({"k": "v"}, [], "k", "exists", []) is True

    def test_key_present_in_expressions(self) -> None:
        assert _label_satisfies_expression({}, [("k", "in", ["v"])], "k", "exists", []) is True

    def test_key_missing(self) -> None:
        assert _label_satisfies_expression({}, [], "k", "exists", []) is False

    def test_key_with_empty_value(self) -> None:
        assert _label_satisfies_expression({"k": ""}, [], "k", "exists", []) is True


class TestLabelSatisfiesExpressionNotExists:
    def test_key_missing(self) -> None:
        assert _label_satisfies_expression({}, [], "k", "!exists", []) is True

    def test_key_present_in_labels(self) -> None:
        assert _label_satisfies_expression({"k": "v"}, [], "k", "!exists", []) is False

    def test_key_present_in_expressions(self) -> None:
        assert _label_satisfies_expression({}, [("k", "in", ["v"])], "k", "!exists", []) is False

    def test_key_with_empty_value(self) -> None:
        assert _label_satisfies_expression({"k": ""}, [], "k", "!exists", []) is False


class TestLabelSatisfiesExpressionNotEqual:
    def test_value_differs(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "!=", ["b"]) is True

    def test_value_matches(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "!=", ["a"]) is False

    def test_key_missing_satisfies_not_equal(self) -> None:
        assert _label_satisfies_expression({}, [], "k", "!=", ["a"]) is True

    def test_empty_values_list(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "!=", []) is True


class TestLabelSatisfiesExpressionNotin:
    def test_value_absent_from_list(self) -> None:
        assert _label_satisfies_expression({"k": "c"}, [], "k", "notin", ["a", "b"]) is True

    def test_value_present_in_list(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "notin", ["a", "b"]) is False

    def test_key_missing_satisfies_notin(self) -> None:
        assert _label_satisfies_expression({}, [], "k", "notin", ["a"]) is True

    def test_empty_values_list(self) -> None:
        assert _label_satisfies_expression({"k": "a"}, [], "k", "notin", []) is True


class TestLabelSatisfiesExpressionUnknownOperator:
    def test_unknown_operator_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"k": "v"}, [], "k", "bogus", ["v"])

    def test_empty_operator_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"k": "v"}, [], "k", "", ["v"])


class TestSelectorContains:
    """Tests for checking if a lease's selector contains a filter's criteria."""

    def test_exact_match_labels(self):
        assert selector_contains("board=rpi", "board=rpi") is True

    def test_subset_match_labels(self):
        assert selector_contains("board=rpi,env=prod", "board=rpi") is True

    def test_no_match_labels(self):
        assert selector_contains("board=jetson", "board=rpi") is False

    def test_exact_match_expressions(self):
        assert selector_contains("firmware in (v2, v3)", "firmware in (v2, v3)") is True

    def test_match_mixed(self):
        lease = "board=rpi,firmware in (v2, v3)"
        assert selector_contains(lease, "board=rpi") is True
        assert selector_contains(lease, "firmware in (v2, v3)") is True
        assert selector_contains(lease, "board=rpi,firmware in (v2, v3)") is True

    def test_no_match_expression(self):
        assert selector_contains("board=rpi", "firmware in (v2, v3)") is False

    def test_filter_not_exists(self):
        assert selector_contains("board=rpi,!experimental", "!experimental") is True
        assert selector_contains("board=rpi", "!experimental") is True

    def test_filter_not_exists_fails_when_key_present(self):
        assert selector_contains("experimental=true", "!experimental") is False

    def test_filter_not_exists_fails_when_key_in_expression(self):
        assert selector_contains("env in (prod, staging)", "!env") is False

    def test_empty_filter_matches_all(self):
        assert selector_contains("board=rpi,firmware in (v2, v3)", "") is True

    def test_whitespace_tolerance(self):
        """Whitespace around operators should be tolerated (matching Go behavior)."""
        assert selector_contains("board=rpi", "board = rpi") is True
        assert selector_contains("board=rpi", "board =rpi") is True
        assert selector_contains("board=rpi", "board= rpi") is True
        assert selector_contains("firmware!=v3", "firmware != v3") is True

    def test_notin_satisfies_when_key_absent(self):
        assert selector_contains("board=rpi", "env notin (dev)") is True

    def test_not_equal_satisfies_when_key_absent(self):
        assert selector_contains("board=rpi", "env!=dev") is True
