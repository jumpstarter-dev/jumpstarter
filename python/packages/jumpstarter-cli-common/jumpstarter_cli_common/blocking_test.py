import pytest

from jumpstarter_cli_common.blocking import blocking


@blocking
async def add(a, b=0):
    return a + b


@blocking
async def fail():
    raise ValueError("boom")


def test_blocking_runs_the_coroutine_with_keyword_arguments():
    assert add(1, b=2) == 3


def test_blocking_propagates_exceptions():
    with pytest.raises(ValueError, match="boom"):
        fail()
