import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


os.environ["TQDM_DISABLE"] = "1"
