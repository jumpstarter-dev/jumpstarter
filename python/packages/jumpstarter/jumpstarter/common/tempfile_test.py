import pytest

from .tempfile import TemporaryTcpListener, TemporaryUnixListener

pytestmark = pytest.mark.anyio


async def _handler(conn):
    await conn.aclose()


@pytest.mark.parametrize("listener", [TemporaryTcpListener, TemporaryUnixListener])
async def test_listener_reraises_body_exception_unwrapped(listener):
    """An error raised in the caller's ``with`` body comes out as itself, not as an ExceptionGroup."""
    error = RuntimeError("Failed to get U-Boot prompt")

    with pytest.raises(RuntimeError) as excinfo:
        async with listener(_handler):
            raise error

    assert excinfo.value is error
    assert excinfo.value.__context__ is None
