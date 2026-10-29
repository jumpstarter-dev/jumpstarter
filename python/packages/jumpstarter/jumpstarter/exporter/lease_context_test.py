from unittest.mock import Mock

import pytest

from jumpstarter.exporter.lease_context import LeaseContext


def test_end_session_is_retained_on_its_lease_and_wakes_once() -> None:
    wake = Mock()
    old = LeaseContext("A", wake=wake)
    replacement = LeaseContext("A", wake=wake)
    old.request_end_session()
    old.request_end_session()
    assert old.end_session_requested and not replacement.end_session_requested
    wake.assert_called_once_with("EndSession")


def test_lease_name_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="non-empty lease_name"):
        LeaseContext("")
