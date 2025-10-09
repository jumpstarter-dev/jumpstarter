from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from .datetime import time_since


def test_time_since_seconds():
    """Test time_since for elapsed time < 1 minute"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=30)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "30s"


def test_time_since_minutes_with_seconds():
    """Test time_since for elapsed time in minutes with seconds"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=5, seconds=30)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "5m30s"


def test_time_since_minutes_without_seconds():
    """Test time_since for elapsed time in exact minutes"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=10)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "10m"


def test_time_since_hours_with_minutes_under_2h():
    """Test time_since for elapsed time in hours with minutes (under 2 hours)"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=1, minutes=30)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "1h30m"


def test_time_since_hours_without_minutes():
    """Test time_since for elapsed time in hours >= 2"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=3, minutes=15)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "3h"


def test_time_since_days_with_hours():
    """Test time_since for elapsed time in days with hours"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=5, hours=6)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "5d6h"


def test_time_since_days_without_hours():
    """Test time_since for elapsed time in exact days"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=10)
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "10d"


def test_time_since_months_with_days():
    """Test time_since for elapsed time in months with days"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=65)  # ~2 months + 5 days
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "2mo5d"


def test_time_since_months_without_days():
    """Test time_since for elapsed time in exact months"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=90)  # Exactly 3 months
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "3mo"


def test_time_since_years_with_months():
    """Test time_since for elapsed time in years with months"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=425)  # ~1 year + 2 months
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "1y2mo"


def test_time_since_years_without_months():
    """Test time_since for elapsed time in exact years"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=730)  # Exactly 2 years
    t_str = past.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch("jumpstarter_kubernetes.datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.strptime.return_value = past.replace(tzinfo=None)
        result = time_since(t_str)
        assert result == "2y"
