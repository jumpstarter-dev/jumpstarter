import pytest

from jumpstarter.common import Metadata

pytestmark = pytest.mark.anyio


async def test_metadata():
    with pytest.raises(ValueError):
        Metadata()

    Metadata(labels={"jumpstarter.dev/name": "test"})
