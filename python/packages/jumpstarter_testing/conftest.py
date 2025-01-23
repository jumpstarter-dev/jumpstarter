import pytest

pytest_plugins = ["pytester"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
