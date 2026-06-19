"""Tests for label selector matching."""

import pytest

from jumpstarter.client.selectors import _label_satisfies_expression, selector_contains


class TestLabelSatisfiesExpressionIn:
    def test_key_present_value_matches(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "in", ["rpi", "jetson"]) is True

    def test_key_present_value_does_not_match(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "in", ["jetson", "nano"]) is False

    def test_key_absent(self):
        assert _label_satisfies_expression({}, "board", "in", ["rpi"]) is False


class TestLabelSatisfiesExpressionNotIn:
    def test_key_present_value_not_in_set(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "notin", ["jetson", "nano"]) is True

    def test_key_present_value_in_set(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "notin", ["rpi", "nano"]) is False

    def test_key_absent(self):
        assert _label_satisfies_expression({}, "board", "notin", ["rpi"]) is True


class TestLabelSatisfiesExpressionExists:
    def test_key_present(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "exists", []) is True

    def test_key_absent(self):
        assert _label_satisfies_expression({}, "board", "exists", []) is False


class TestLabelSatisfiesExpressionDoesNotExist:
    def test_key_present(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "!exists", []) is False

    def test_key_absent(self):
        assert _label_satisfies_expression({}, "board", "!exists", []) is True


class TestLabelSatisfiesExpressionNotEqual:
    def test_key_present_value_differs(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "!=", ["jetson"]) is True

    def test_key_present_value_same(self):
        assert _label_satisfies_expression({"board": "rpi"}, "board", "!=", ["rpi"]) is False

    def test_key_absent(self):
        assert _label_satisfies_expression({}, "board", "!=", ["rpi"]) is True


class TestLabelSatisfiesExpressionUnknownOperator:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"key": "val"}, "key", "bogus", ["val"])

    def test_error_message_includes_operator(self):
        with pytest.raises(ValueError, match="'bogus'"):
            _label_satisfies_expression({"key": "val"}, "key", "bogus", ["val"])

    def test_empty_string_operator_raises(self):
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"key": "val"}, "key", "", ["val"])


class TestSelectorContains:

    def test_exact_match_labels(self):
        assert selector_contains("board=rpi", "board=rpi") is True

    def test_subset_match_labels(self):
        assert selector_contains("board=rpi,env=prod", "board=rpi") is True

    def test_double_equals_match(self):
        assert selector_contains("board=rpi", "board==rpi") is True

    def test_double_equals_in_selector(self):
        assert selector_contains("board==rpi", "board=rpi") is True

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

    def test_filter_not_exists_present_in_selector(self):
        assert selector_contains("board=rpi,!experimental", "!experimental") is True

    def test_filter_not_exists_absent_from_selector(self):
        assert selector_contains("board=rpi", "!experimental") is True

    def test_filter_not_exists_key_present_in_labels(self):
        assert selector_contains("experimental=true", "!experimental") is False

    def test_empty_filter_matches_all(self):
        assert selector_contains("board=rpi,firmware in (v2, v3)", "") is True

    def test_match_label_satisfies_in_expression(self):
        assert selector_contains("board=rpi", "board in (rpi, jetson)") is True

    def test_match_label_does_not_satisfy_in_expression(self):
        assert selector_contains("board=rpi", "board in (jetson, nano)") is False

    def test_match_label_satisfies_notin_expression(self):
        assert selector_contains("board=rpi", "board notin (jetson, nano)") is True

    def test_match_label_does_not_satisfy_notin_expression(self):
        assert selector_contains("board=rpi", "board notin (rpi, nano)") is False

    def test_notin_key_absent_from_selector(self):
        assert selector_contains("board=rpi", "env notin (prod)") is True

    def test_not_equal_key_absent_from_selector(self):
        assert selector_contains("board=rpi", "env!=prod") is True

    def test_exists_key_present_in_selector(self):
        assert selector_contains("board=rpi", "board") is True

    def test_exists_key_absent_from_selector(self):
        assert selector_contains("board=rpi", "env") is False

    def test_whitespace_tolerance(self):
        assert selector_contains("board=rpi", "board = rpi") is True
        assert selector_contains("board=rpi", "board =rpi") is True
        assert selector_contains("board=rpi", "board= rpi") is True
        assert selector_contains("firmware!=v3", "firmware != v3") is True
