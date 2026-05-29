from hypothesis import given, settings
from hypothesis import strategies as st

from .selectors import parse_label_selector, selector_contains

selector_key = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_./-]{0,30}", fullmatch=True)
selector_value = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,30}", fullmatch=True)


def label_pairs_strategy(min_size: int = 1, max_size: int = 5):
    return st.dictionaries(keys=selector_key, values=selector_value, min_size=min_size, max_size=max_size)


def format_selector(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in labels.items())


class TestParseLabelSelectorRoundtrip:
    @given(labels=label_pairs_strategy())
    @settings(max_examples=50)
    def test_parsed_labels_contain_originals(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        parsed_labels, _ = parse_label_selector(selector_str)
        for key, value in labels.items():
            assert key in parsed_labels, f"Missing key {key!r} from parsed labels"
            assert parsed_labels[key] == value

    @given(labels=label_pairs_strategy())
    @settings(max_examples=50)
    def test_parsed_label_count_matches(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        parsed_labels, parsed_exprs = parse_label_selector(selector_str)
        assert len(parsed_labels) == len(labels)
        assert len(parsed_exprs) == 0


class TestSelectorContainsProperties:
    @given(labels=label_pairs_strategy())
    @settings(max_examples=50)
    def test_reflexivity(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        assert selector_contains(selector_str, selector_str) is True

    @given(
        all_labels=label_pairs_strategy(min_size=2, max_size=6),
        data=st.data(),
    )
    @settings(max_examples=50)
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
    @settings(max_examples=50)
    def test_empty_requirements_always_matches(self, labels: dict[str, str]) -> None:
        selector_str = format_selector(labels)
        assert selector_contains(selector_str, "") is True

    @given(
        labels_a=label_pairs_strategy(),
        labels_b=label_pairs_strategy(),
    )
    @settings(max_examples=50)
    def test_disjoint_labels_do_not_match(self, labels_a: dict[str, str], labels_b: dict[str, str]) -> None:
        disjoint_b = {k: v for k, v in labels_b.items() if k not in labels_a}
        if not disjoint_b:
            return
        selector_a = format_selector(labels_a)
        requirements_b = format_selector(disjoint_b)
        assert selector_contains(selector_a, requirements_b) is False


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
    @settings(max_examples=30)
    def test_in_expression_parsed(self, key: str, values: list[str]) -> None:
        expr_str = f"{key} in ({', '.join(values)})"
        _, exprs = parse_label_selector(expr_str)
        assert len(exprs) == 1
        assert exprs[0][0] == key
        assert exprs[0][1] == "in"
        assert set(exprs[0][2]) == set(values)
