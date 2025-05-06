import pytest
from click.testing import CliRunner

from . import driver


@pytest.mark.anyio
async def test_list_drivers():
    runner = CliRunner()

    result = await runner.invoke(
        driver,
        ["list"],
    )
    assert result.exit_code == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
