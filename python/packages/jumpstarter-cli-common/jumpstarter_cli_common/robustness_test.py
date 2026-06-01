from typing import Any, cast

import click
from hypothesis import given
from hypothesis import strategies as st

from .opt import (
    _normalize_tokens,
    _opt_labels_callback,
    _validate_tokens,
    parse_comma_separated,
    validate_name,
)
from jumpstarter.testing_strategies import ARBITRARY


class TestLabelsCallbackRobustness:
    @given(value=st.tuples(st.text(max_size=100)))
    def test_labels_callback_never_crashes_on_text(self, value: tuple[str]) -> None:
        try:
            _opt_labels_callback(None, None, value)
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"_opt_labels_callback raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(values=st.lists(st.text(max_size=100), max_size=10))
    def test_labels_callback_never_crashes_on_list(self, values: list[str]) -> None:
        try:
            result = _opt_labels_callback(None, None, tuple(values))
            assert isinstance(result, dict)
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"_opt_labels_callback raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(
        value=st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz=\x00\n\r\t\\\"'`$(){}|&;!@#%^*<>~")),
            max_size=100,
        )
    )
    def test_labels_callback_handles_shell_metacharacters(self, value: str) -> None:
        try:
            _opt_labels_callback(None, None, (value,))
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"_opt_labels_callback raised unexpected {type(exc).__name__}: {exc}") from exc


class TestParseCommaSeparatedRobustness:
    @given(value=st.text(max_size=200))
    def test_parse_comma_separated_never_crashes_on_text(self, value: str) -> None:
        try:
            result = parse_comma_separated(None, None, value)
            assert isinstance(result, list)
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"parse_comma_separated raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(values=st.tuples(st.text(max_size=100), st.text(max_size=100)))
    def test_parse_comma_separated_never_crashes_on_tuple(self, values: tuple[str, str]) -> None:
        try:
            result = parse_comma_separated(None, None, values)
            assert isinstance(result, list)
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"parse_comma_separated raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(
        value=st.text(max_size=200),
        allowed=st.frozensets(st.text(min_size=1, max_size=20), max_size=10),
    )
    def test_parse_comma_separated_with_allowed_values(self, value: str, allowed: frozenset[str]) -> None:
        try:
            parse_comma_separated(None, None, value, allowed_values=set(allowed))
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"parse_comma_separated raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=ARBITRARY)
    def test_parse_comma_separated_never_crashes_on_arbitrary(self, value: object) -> None:
        try:
            parse_comma_separated(None, None, cast(Any, value))
        except (click.BadParameter, TypeError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"parse_comma_separated raised unexpected {type(exc).__name__}: {exc}") from exc


class TestValidateNameRobustness:
    @given(name=st.text(max_size=200))
    def test_validate_name_never_crashes_on_text(self, name: str) -> None:
        try:
            validate_name(name)
        except click.UsageError:
            pass
        except Exception as exc:
            raise AssertionError(f"validate_name raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(name=ARBITRARY)
    def test_validate_name_never_crashes_on_arbitrary(self, name: object) -> None:
        try:
            validate_name(cast(Any, name))
        except (click.UsageError, TypeError, AttributeError):
            pass
        except Exception as exc:
            raise AssertionError(f"validate_name raised unexpected {type(exc).__name__}: {exc}") from exc


class TestNormalizeTokensRobustness:
    @given(
        items=st.lists(st.text(max_size=100), max_size=20),
        normalize_case=st.booleans(),
    )
    def test_normalize_tokens_never_crashes(self, items: list[str], normalize_case: bool) -> None:
        try:
            result = _normalize_tokens(items, normalize_case)
            assert isinstance(result, list)
        except Exception as exc:
            raise AssertionError(f"_normalize_tokens raised unexpected {type(exc).__name__}: {exc}") from exc


class TestValidateTokensRobustness:
    @given(
        tokens=st.lists(st.text(max_size=50), max_size=20),
        allowed=st.frozensets(st.text(min_size=1, max_size=20), max_size=10),
    )
    def test_validate_tokens_never_crashes(self, tokens: list[str], allowed: frozenset[str]) -> None:
        try:
            _validate_tokens(tokens, set(allowed), None, None)
        except click.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"_validate_tokens raised unexpected {type(exc).__name__}: {exc}") from exc
