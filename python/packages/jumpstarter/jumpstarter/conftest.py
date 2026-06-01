import grpc
import pytest
from grpc import StatusCode
from grpc.aio import AioRpcError


class MockAioRpcError(AioRpcError):
    def __init__(self, status_code: StatusCode, message: str = ""):
        self._code = status_code
        self._details = message
        self._debug_error_string = ""
        self._initial_metadata = grpc.aio.Metadata()
        self._trailing_metadata = grpc.aio.Metadata()

    def code(self) -> StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


@pytest.fixture
def mock_aio_rpc_error():
    return MockAioRpcError
