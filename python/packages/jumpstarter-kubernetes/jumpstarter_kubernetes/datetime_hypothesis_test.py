from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from .datetime import time_since

past_offsets = st.integers(min_value=1, max_value=5 * 365 * 86400)


class TestTimeSinceProperty:
    @given(seconds_ago=st.integers(min_value=1, max_value=59))
    def test_seconds_range_returns_seconds_suffix(self, seconds_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(seconds=seconds_ago)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert result.endswith("s")
            assert "m" not in result
            assert "h" not in result

    @given(minutes_ago=st.integers(min_value=1, max_value=59))
    def test_minutes_range_contains_m_suffix(self, minutes_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(minutes=minutes_ago)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert "m" in result
            assert "h" not in result

    @given(hours_ago=st.integers(min_value=1, max_value=23))
    def test_hours_range_contains_h_suffix(self, hours_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(hours=hours_ago)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert "h" in result

    @given(days_ago=st.integers(min_value=1, max_value=29))
    def test_days_range_contains_d_suffix(self, days_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=days_ago)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert "d" in result

    @given(months_ago=st.integers(min_value=1, max_value=11))
    def test_months_range_contains_mo_suffix(self, months_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=months_ago * 30 + 1)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert "mo" in result

    @given(years_ago=st.integers(min_value=1, max_value=5))
    def test_years_range_contains_y_suffix(self, years_ago: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(days=years_ago * 365 + 1)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert "y" in result

    @given(offset=past_offsets)
    def test_output_never_empty(self, offset: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(seconds=offset)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            assert len(result) > 0

    @given(offset=past_offsets)
    def test_output_contains_only_valid_characters(self, offset: int) -> None:
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = now - timedelta(seconds=offset)
        t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

        with patch("jumpstarter_kubernetes.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime.return_value = past.replace(tzinfo=None)
            result = time_since(t_str)
            valid_chars = set("0123456789smhdoy")
            assert all(c in valid_chars for c in result), f"Unexpected chars in: {result}"
