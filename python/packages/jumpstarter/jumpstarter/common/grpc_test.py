import grpc
import pytest
from grpc import StatusCode
from grpc.aio import AioRpcError

from jumpstarter.common.exceptions import ConnectionError
from jumpstarter.common.grpc import _override_default_grpc_options, translate_grpc_exceptions


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


def test_default_options_preserve_existing_defaults():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.lb_policy_name"] == "round_robin"
    assert options["grpc.keepalive_time_ms"] == 20000



def test_user_options_override_defaults():
    user_options = {"grpc.keepalive_time_ms": 50000}
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.keepalive_time_ms"] == 50000


def test_default_options_include_unlimited_max_receive_message_length():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.max_receive_message_length"] == -1


def test_default_options_include_unlimited_max_send_message_length():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.max_send_message_length"] == -1


def test_user_can_override_max_message_lengths():
    user_options = {
        "grpc.max_receive_message_length": 1024,
        "grpc.max_send_message_length": 2048,
    }
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.max_receive_message_length"] == 1024
    assert options["grpc.max_send_message_length"] == 2048


def test_translate_grpc_exceptions_deadline_exceeded_raises_connection_error():
    with pytest.raises(ConnectionError, match="deadline exceeded"):
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "deadline hit")


def test_translate_grpc_exceptions_deadline_exceeded_includes_details():
    with pytest.raises(ConnectionError) as exc_info:
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "operation timed out")
    assert "operation timed out" in str(exc_info.value)
