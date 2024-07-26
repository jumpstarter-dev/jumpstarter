import pytest

from jumpstarter.common import Metadata


def test_metadata():
    with pytest.raises(ValueError):
        Metadata()

    Metadata(labels={"jumpstarter.dev/name": "test"})
