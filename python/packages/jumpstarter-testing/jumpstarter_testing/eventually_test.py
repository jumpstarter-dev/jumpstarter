import pytest

from jumpstarter_testing.eventually import eventually


def test_eventually_succeeds_immediately():
    """Callable that passes on the first try returns immediately."""
    called = []
    def fn():
        called.append(1)
    eventually(fn, timeout=1)
    assert len(called) == 1


def test_eventually_retries_until_success():
    """Callable that fails initially but succeeds after a few attempts."""
    counter = {"n": 0}
    def fn():
        counter["n"] += 1
        if counter["n"] < 3:
            raise AssertionError("not yet")
    eventually(fn, timeout=5, interval=0.01)
    assert counter["n"] == 3


def test_eventually_raises_on_timeout():
    """Callable that never succeeds raises the last error after timeout."""
    def fn():
        raise AssertionError("always fails")
    with pytest.raises(AssertionError, match="always fails"):
        eventually(fn, timeout=0.1, interval=0.02)
