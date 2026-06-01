"""Tests for label selector matching."""

import pytest

from jumpstarter.client.selectors import _label_satisfies_expression, selector_contains


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
        assert selector_contains("board=rpi", "!experimental") is False

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

    def test_whitespace_tolerance(self):
        """Whitespace around operators should be tolerated (matching Go behavior)."""
        assert selector_contains("board=rpi", "board = rpi") is True
        assert selector_contains("board=rpi", "board =rpi") is True
        assert selector_contains("board=rpi", "board= rpi") is True
        assert selector_contains("firmware!=v3", "firmware != v3") is True


class TestLabelSatisfiesExpressionUnknownOperator:
    """Tests for _label_satisfies_expression raising ValueError on unknown operators."""

    def test_empty_string_operator_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"key": "value"}, "key", "", ["value"])

    def test_invalid_operator_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"key": "value"}, "key", "invalid", ["value"])

    def test_equals_operator_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown label selector operator"):
            _label_satisfies_expression({"key": "value"}, "key", "=", ["value"])

    def test_not_exists_operator_returns_false_when_key_present(self):
        assert _label_satisfies_expression({"key": "value"}, "key", "!exists", []) is False

    def test_error_message_includes_operator(self):
        with pytest.raises(ValueError, match="'bogus'"):
            _label_satisfies_expression({"key": "value"}, "key", "bogus", ["value"])

    def test_in_operator_still_works(self):
        assert _label_satisfies_expression({"key": "value"}, "key", "in", ["value"]) is True
        assert _label_satisfies_expression({"key": "value"}, "key", "in", ["other"]) is False

    def test_notin_operator_still_works(self):
        assert _label_satisfies_expression({"key": "value"}, "key", "notin", ["other"]) is True
        assert _label_satisfies_expression({"key": "value"}, "key", "notin", ["value"]) is False

    def test_exists_operator_still_works(self):
        assert _label_satisfies_expression({"key": "value"}, "key", "exists", []) is True

    def test_not_equal_operator_still_works(self):
        assert _label_satisfies_expression({"key": "value"}, "key", "!=", ["other"]) is True
        assert _label_satisfies_expression({"key": "value"}, "key", "!=", ["value"]) is False

    def test_key_not_in_labels_returns_false(self):
        assert _label_satisfies_expression({}, "missing", "in", ["value"]) is False
