from hypothesis import given
from hypothesis import strategies as st

from .selectors import extract_match_labels_filter, parse_label_selector, selector_contains
from jumpstarter.testing_strategies import label_key as selector_key
from jumpstarter.testing_strategies import label_value as selector_value


def label_pairs_strategy(min_size: int = 1, max_size: int = 5) -> st.SearchStrategy[dict[str, str]]:
    return st.dictionaries(keys=selector_key, values=selector_value, min_size=min_size, max_size=max_size)


def format_selector(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in labels.items())


class TestParseLabelSelectorRoundtrip:
    @given(labels=label_pairs_strategy())
    def test_parsed_labels_contain_originals(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        parsed_labels, _ = parse_label_selector(selector_str)
        for key, value in labels.items():
            assert key in parsed_labels, f"Missing key {key!r} from parsed labels"
            assert parsed_labels[key] == value

    @given(labels=label_pairs_strategy())
    def test_parsed_label_count_matches(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        parsed_labels, parsed_exprs = parse_label_selector(selector_str)
        assert len(parsed_labels) == len(labels)
        assert len(parsed_exprs) == 0


class TestSelectorContainsProperties:
    @given(labels=label_pairs_strategy())
    def test_reflexivity(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        assert selector_contains(selector_str, selector_str) is True

    @given(
        all_labels=label_pairs_strategy(min_size=2, max_size=6),
        data=st.data(),
    )
    def test_superset_contains_subset(self, all_labels: dict[str, str], data: st.DataObject) -> None:
        keys = list(all_labels.keys())
        subset_size = data.draw(st.integers(min_value=1, max_value=len(keys) - 1))
        subset_keys = data.draw(
            st.lists(st.sampled_from(keys), min_size=subset_size, max_size=subset_size, unique=True)
        )
        subset = {k: all_labels[k] for k in subset_keys}

        full_selector = format_selector(all_labels)
        sub_selector = format_selector(subset)
        assert selector_contains(full_selector, sub_selector) is True

    @given(labels=label_pairs_strategy())
    def test_empty_requirements_always_matches(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        assert selector_contains(selector_str, "") is True

    @given(
        labels_a=label_pairs_strategy(),
        labels_b=label_pairs_strategy(),
    )
    def test_disjoint_labels_do_not_match(self, labels_a: dict[str, str], labels_b: dict[str, str]) -> None:
        disjoint_b = {k: v for k, v in labels_b.items() if k not in labels_a}
        if not disjoint_b:
            return
        selector_a = format_selector(labels_a)
        requirements_b = format_selector(disjoint_b)
        assert selector_contains(selector_a, requirements_b) is False

    @given(key=selector_key, values=st.lists(selector_value, min_size=1, max_size=5))
    def test_in_expression_reflexivity(self, key: str, values: list[str]) -> None:
        expr = f"{key} in ({', '.join(values)})"
        assert selector_contains(expr, expr) is True

    @given(key=selector_key, values=st.lists(selector_value, min_size=1, max_size=5))
    def test_notin_expression_reflexivity(self, key: str, values: list[str]) -> None:
        expr = f"{key} notin ({', '.join(values)})"
        assert selector_contains(expr, expr) is True

    @given(key=selector_key)
    def test_exists_expression_reflexivity(self, key: str) -> None:
        assert selector_contains(key, key) is True

    @given(key=selector_key)
    def test_not_exists_expression_reflexivity(self, key: str) -> None:
        expr = f"!{key}"
        assert selector_contains(expr, expr) is True

    @given(
        present_labels=label_pairs_strategy(),
        absent_key=selector_key,
    )
    def test_absent_key_satisfies_not_exists(self, present_labels: dict[str, str], absent_key: str) -> None:
        if absent_key in present_labels:
            return
        selector_str = format_selector(present_labels)
        requirement = f"!{absent_key}"
        assert selector_contains(selector_str, requirement) is True

    @given(
        present_labels=label_pairs_strategy(),
        absent_key=selector_key,
        excluded_values=st.lists(selector_value, min_size=1, max_size=3),
    )
    def test_absent_key_satisfies_notin(self, present_labels: dict[str, str], absent_key: str, excluded_values: list[str]) -> None:
        if absent_key in present_labels:
            return
        selector_str = format_selector(present_labels)
        requirement = f"{absent_key} notin ({', '.join(excluded_values)})"
        assert selector_contains(selector_str, requirement) is True

    @given(
        present_labels=label_pairs_strategy(),
        absent_key=selector_key,
        value=selector_value,
    )
    def test_absent_key_satisfies_not_equal(self, present_labels: dict[str, str], absent_key: str, value: str) -> None:
        if absent_key in present_labels:
            return
        selector_str = format_selector(present_labels)
        requirement = f"{absent_key}!={value}"
        assert selector_contains(selector_str, requirement) is True

    @given(key=selector_key, value=selector_value)
    def test_mismatched_operators_do_not_match(self, key: str, value: str) -> None:
        selector = f"{key} in ({value})"
        requirement = f"!{key}"
        assert selector_contains(selector, requirement) is False

    @given(key=selector_key, value=selector_value)
    def test_label_selector_satisfies_in_requirement(self, key: str, value: str) -> None:
        selector = f"{key}={value}"
        requirement = f"{key} in ({value})"
        assert selector_contains(selector, requirement) is True

    @given(key=selector_key, value=selector_value)
    def test_label_selector_satisfies_exists_requirement(self, key: str, value: str) -> None:
        selector = f"{key}={value}"
        requirement = key
        assert selector_contains(selector, requirement) is True

    @given(
        key=selector_key,
        value=selector_value,
        other=selector_value,
    )
    def test_label_selector_satisfies_not_equal_requirement(self, key: str, value: str, other: str) -> None:
        if value == other:
            return
        selector = f"{key}={value}"
        requirement = f"{key}!={other}"
        assert selector_contains(selector, requirement) is True

    @given(
        key=selector_key,
        value=selector_value,
        other=selector_value,
    )
    def test_label_selector_satisfies_notin_requirement(self, key: str, value: str, other: str) -> None:
        if value == other:
            return
        selector = f"{key}={value}"
        requirement = f"{key} notin ({other})"
        assert selector_contains(selector, requirement) is True


class TestParseLabelSelectorEdgeCases:
    def test_empty_string(self) -> None:
        labels, exprs = parse_label_selector("")
        assert labels == {}
        assert exprs == []

    def test_whitespace_only(self) -> None:
        labels, exprs = parse_label_selector("   ")
        assert labels == {}
        assert exprs == []

    @given(key=selector_key, values=st.lists(selector_value, min_size=1, max_size=5))
    def test_in_expression_parsed(self, key: str, values: list[str]) -> None:
        expr_str = f"{key} in ({', '.join(values)})"
        _, exprs = parse_label_selector(expr_str)
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "in"
        assert set(exprs[0][2]) == set(values)

    @given(key=selector_key, value=selector_value)
    def test_not_equal_expression_parsed(self, key: str, value: str) -> None:
        _, exprs = parse_label_selector(f"{key}!={value}")
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "!="
        assert exprs[0][2] == [value]

    @given(key=selector_key, values=st.lists(selector_value, min_size=1, max_size=5))
    def test_notin_expression_parsed(self, key: str, values: list[str]) -> None:
        expr_str = f"{key} notin ({', '.join(values)})"
        _, exprs = parse_label_selector(expr_str)
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "notin"
        assert set(exprs[0][2]) == set(values)

    @given(key=selector_key)
    def test_exists_expression_parsed(self, key: str) -> None:
        _, exprs = parse_label_selector(key)
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "exists"
        assert exprs[0][2] == []

    @given(key=selector_key)
    def test_not_exists_expression_parsed(self, key: str) -> None:
        _, exprs = parse_label_selector(f"!{key}")
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "!exists"
        assert exprs[0][2] == []


class TestExtractMatchLabelsFilter:
    @given(labels=label_pairs_strategy())
    def test_roundtrip_preserves_labels(self, labels: dict[str, str]) -> None:
        selector = format_selector(labels)
        result = extract_match_labels_filter(selector)
        assert result is not None
        parsed_labels, _ = parse_label_selector(result)
        assert parsed_labels == labels

    def test_none_input_returns_none(self) -> None:
        assert extract_match_labels_filter(None) is None

    def test_empty_input_returns_none(self) -> None:
        assert extract_match_labels_filter("") is None

    @given(key=selector_key)
    def test_expression_only_returns_none(self, key: str) -> None:
        assert extract_match_labels_filter(key) is None
