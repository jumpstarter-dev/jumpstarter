import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
# TEST, do not merge
