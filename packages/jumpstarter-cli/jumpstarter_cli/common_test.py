from datetime import datetime, timedelta, timezone

import click
import pytest

from jumpstarter_cli.common import DATETIME, DURATION, DateTimeParamType, DurationParamType


class TestDateTimeParamType:
    """Test DateTimeParamType parameter parsing and normalization."""

    def test_parse_iso8601_with_timezone(self):
        """Test parsing ISO 8601 datetime with timezone."""
        dt = DATETIME.convert("2024-01-01T12:00:00Z", None, None)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 12
        assert dt.minute == 0
        assert dt.second == 0
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_parse_iso8601_naive_gets_normalized(self):
        """Test that naive datetime gets normalized to local timezone."""
        dt = DATETIME.convert("2024-01-01T12:00:00", None, None)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 12
        assert dt.minute == 0
        assert dt.second == 0
        # Should have been normalized to local timezone
        assert dt.tzinfo is not None

    def test_pass_through_datetime_object_with_timezone(self):
        """Test that datetime object with timezone passes through."""
        input_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        dt = DATETIME.convert(input_dt, None, None)
        assert dt == input_dt
        assert dt.tzinfo == timezone.utc

    def test_pass_through_datetime_object_naive_gets_normalized(self):
        """Test that naive datetime object gets normalized."""
        input_dt = datetime(2024, 1, 1, 12, 0, 0)  # Naive
        dt = DATETIME.convert(input_dt, None, None)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 12
        # Should have been normalized to local timezone
        assert dt.tzinfo is not None

    def test_invalid_datetime_raises_click_exception(self):
        """Test that invalid datetime string raises click exception."""
        param_type = DateTimeParamType()
        with pytest.raises(click.BadParameter, match="is not a valid datetime"):
            param_type.convert("not-a-datetime", None, None)


class TestDurationParamType:
    """Test DurationParamType parameter parsing."""

    def test_parse_iso8601_duration(self):
        """Test parsing ISO 8601 duration."""
        td = DURATION.convert("PT1H30M", None, None)
        assert td == timedelta(hours=1, minutes=30)

    def test_parse_time_format(self):
        """Test parsing HH:MM:SS format."""
        td = DURATION.convert("01:30:00", None, None)
        assert td == timedelta(hours=1, minutes=30)

    def test_parse_days_and_time(self):
        """Test parsing 'D days, HH:MM:SS' format."""
        td = DURATION.convert("2 days, 01:30:00", None, None)
        assert td == timedelta(days=2, hours=1, minutes=30)

    def test_pass_through_timedelta_object(self):
        """Test that timedelta object passes through."""
        input_td = timedelta(hours=1, minutes=30)
        td = DURATION.convert(input_td, None, None)
        assert td == input_td

    def test_invalid_duration_raises_click_exception(self):
        """Test that invalid duration string raises click exception."""
        param_type = DurationParamType()
        with pytest.raises(click.BadParameter, match="is not a valid duration"):
            param_type.convert("not-a-duration", None, None)

