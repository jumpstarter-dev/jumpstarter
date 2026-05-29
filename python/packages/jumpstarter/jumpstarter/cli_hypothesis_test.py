from datetime import timedelta

import click
import pytest
from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_cli.common import DURATION, DateTimeParamType, DurationParamType
from jumpstarter_cli_common.opt import parse_comma_separated


class TestDurationParamTypeWithFuzzedIntegers:
    @given(seconds=st.integers(min_value=0, max_value=86400 * 365))
    def test_integer_seconds_produce_valid_timedelta(self, seconds: int) -> None:
        result = DURATION.convert(seconds, None, None)
        assert result == timedelta(seconds=seconds)

    @given(seconds=st.integers(min_value=0, max_value=86400 * 365))
    def test_string_integer_seconds_produce_valid_timedelta(self, seconds: int) -> None:
        result = DURATION.convert(str(seconds), None, None)
        assert result == timedelta(seconds=seconds)


class TestDurationParamTypeWithHumanReadable:
    @given(
        hours=st.integers(min_value=0, max_value=999),
        minutes=st.integers(min_value=0, max_value=59),
    )
    def test_hhmm_format_produces_correct_timedelta(self, hours: int, minutes: int) -> None:
        duration_str = f"{hours}h{minutes}m"
        result = DURATION.convert(duration_str, None, None)
        expected = timedelta(hours=hours, minutes=minutes)
        assert result == expected

    @given(minutes=st.integers(min_value=1, max_value=10000))
    def test_minutes_only_format(self, minutes: int) -> None:
        result = DURATION.convert(f"{minutes}m", None, None)
        assert result == timedelta(minutes=minutes)

    @given(hours=st.integers(min_value=1, max_value=10000))
    def test_hours_only_format(self, hours: int) -> None:
        result = DURATION.convert(f"{hours}h", None, None)
        assert result == timedelta(hours=hours)


class TestDurationParamTypeWithISO8601:
    @given(
        hours=st.integers(min_value=0, max_value=99),
        minutes=st.integers(min_value=0, max_value=59),
    )
    def test_iso8601_pt_format(self, hours: int, minutes: int) -> None:
        iso_str = f"PT{hours}H{minutes}M"
        result = DURATION.convert(iso_str, None, None)
        expected = timedelta(hours=hours, minutes=minutes)
        assert result == expected


class TestDurationParamTypeRejectsInvalid:
    @given(
        text=st.text(
            alphabet=st.characters(categories=("L",), min_codepoint=0x61, max_codepoint=0x7A),
            min_size=3,
            max_size=20,
        ).filter(lambda s: not any(c.isdigit() for c in s))
    )
    def test_non_numeric_non_duration_strings_are_rejected(self, text: str) -> None:
        param_type = DurationParamType()
        with pytest.raises(click.BadParameter):
            param_type.convert(text, None, None)


class TestDurationParamTypeMinimum:
    @given(seconds=st.integers(min_value=0, max_value=4))
    def test_below_minimum_is_rejected(self, seconds: int) -> None:
        param_type = DurationParamType(minimum=timedelta(seconds=5))
        with pytest.raises(click.BadParameter, match="at least"):
            param_type.convert(seconds, None, None)

    @given(seconds=st.integers(min_value=5, max_value=1000))
    def test_at_or_above_minimum_is_accepted(self, seconds: int) -> None:
        param_type = DurationParamType(minimum=timedelta(seconds=5))
        result = param_type.convert(seconds, None, None)
        assert result == timedelta(seconds=seconds)


class TestDurationParamTypePassthrough:
    @given(seconds=st.integers(min_value=0, max_value=86400))
    def test_timedelta_passthrough(self, seconds: int) -> None:
        td = timedelta(seconds=seconds)
        result = DURATION.convert(td, None, None)
        assert result == td


class TestDateTimeParamTypeWithFuzzedStrings:
    @given(
        year=st.integers(min_value=2000, max_value=2099),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
    )
    def test_iso8601_datetime_parses(self, year: int, month: int, day: int, hour: int, minute: int) -> None:
        dt_str = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00Z"
        param_type = DateTimeParamType()
        result = param_type.convert(dt_str, None, None)
        assert result.year == year
        assert result.month == month
        assert result.day == day
        assert result.hour == hour
        assert result.minute == minute
        assert result.tzinfo is not None

    @given(
        text=st.text(
            alphabet=st.characters(categories=("L",), min_codepoint=0x61, max_codepoint=0x7A),
            min_size=5,
            max_size=20,
        )
    )
    def test_non_datetime_strings_are_rejected(self, text: str) -> None:
        param_type = DateTimeParamType()
        with pytest.raises(click.BadParameter):
            param_type.convert(text, None, None)


class TestParseCommaSeparatedWithFuzzedInput:
    @given(values=st.lists(st.from_regex(r"[a-z]{1,10}", fullmatch=True), min_size=1, max_size=5))
    def test_tuple_input_returns_all_values(self, values: list[str]) -> None:
        result = parse_comma_separated(None, None, tuple(values))
        for v in values:
            assert v in result

    @given(values=st.lists(st.from_regex(r"[a-z]{1,10}", fullmatch=True), min_size=1, max_size=5))
    def test_csv_string_returns_all_values(self, values: list[str]) -> None:
        csv_str = ",".join(values)
        result = parse_comma_separated(None, None, csv_str)
        for v in values:
            assert v in result

    @given(value=st.from_regex(r"[a-z]{1,10}", fullmatch=True))
    def test_single_value_returns_list(self, value: str) -> None:
        result = parse_comma_separated(None, None, value)
        assert value in result
        assert isinstance(result, list)

    def test_empty_value_returns_empty_list(self) -> None:
        assert parse_comma_separated(None, None, None) == []
        assert parse_comma_separated(None, None, "") == []

    @given(values=st.lists(st.from_regex(r"[a-z]{1,10}", fullmatch=True), min_size=1, max_size=5))
    def test_duplicates_are_removed(self, values: list[str]) -> None:
        doubled = values + values
        result = parse_comma_separated(None, None, tuple(doubled))
        assert len(result) == len(set(values))

    @given(
        values=st.lists(st.from_regex(r"[a-z]{1,10}", fullmatch=True), min_size=1, max_size=3),
        allowed=st.frozensets(st.from_regex(r"[a-z]{1,10}", fullmatch=True), min_size=3, max_size=10),
    )
    def test_invalid_values_raise_bad_parameter(self, values: list[str], allowed: frozenset[str]) -> None:
        invalid = [v for v in values if v not in allowed]
        if invalid:
            with pytest.raises(click.BadParameter):
                parse_comma_separated(None, None, tuple(values), allowed_values=set(allowed))


class TestParseCommaSeparatedCaseNormalization:
    @given(value=st.from_regex(r"[A-Z]{1,10}", fullmatch=True))
    def test_normalize_case_lowercases(self, value: str) -> None:
        result = parse_comma_separated(None, None, value, normalize_case=True)
        assert all(v == v.lower() for v in result)

    @given(value=st.from_regex(r"[A-Z]{1,10}", fullmatch=True))
    def test_no_normalize_preserves_case(self, value: str) -> None:
        result = parse_comma_separated(None, None, value, normalize_case=False)
        assert value in result


class TestLabelParsingWithFuzzedInput:
    @given(
        key=st.from_regex(r"[a-zA-Z][a-zA-Z0-9._-]{0,20}", fullmatch=True),
        value=st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,20}", fullmatch=True),
    )
    def test_valid_label_parses(self, key: str, value: str) -> None:
        from jumpstarter_cli_common.opt import _opt_labels_callback

        result = _opt_labels_callback(None, None, (f"{key}={value}",))
        assert result[key] == value

    @given(label=st.from_regex(r"[a-zA-Z][a-zA-Z0-9]{0,10}", fullmatch=True))
    def test_label_without_equals_raises(self, label: str) -> None:
        from jumpstarter_cli_common.opt import _opt_labels_callback

        with pytest.raises(click.BadParameter, match="Invalid label"):
            _opt_labels_callback(None, None, (label,))
