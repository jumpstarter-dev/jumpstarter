import pytest

from . import export
from .decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMING_DRIVERCALL,
)


class Functions:
    @export
    def function(self):
        pass

    @export
    async def asyncfunction(self):
        pass

    @export
    def generator(self):
        yield

    @export
    async def asyncgenerator(self):
        yield


def test_driver_decorators():
    functions = Functions()

    assert getattr(functions.function, MARKER_DRIVERCALL) == MARKER_MAGIC
    assert getattr(functions.asyncfunction, MARKER_DRIVERCALL) == MARKER_MAGIC
    assert getattr(functions.generator, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC
    assert getattr(functions.asyncgenerator, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC

    with pytest.raises(ValueError):
        export(None)
