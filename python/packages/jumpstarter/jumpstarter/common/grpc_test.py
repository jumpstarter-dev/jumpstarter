import pytest
from grpc import StatusCode

from jumpstarter.common.exceptions import ConnectionError
from jumpstarter.common.grpc import _override_default_grpc_options, translate_grpc_exceptions
from jumpstarter.conftest import MockAioRpcError


def test_default_options_preserve_existing_defaults():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.lb_policy_name"] == "round_robin"
    assert options["grpc.keepalive_time_ms"] == 20000



def test_user_options_override_defaults():
    user_options = {"grpc.keepalive_time_ms": 50000}
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.keepalive_time_ms"] == 50000



def test_translate_grpc_exceptions_deadline_exceeded_raises_connection_error():
    with pytest.raises(ConnectionError, match="deadline exceeded"):
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "deadline hit")


def test_translate_grpc_exceptions_deadline_exceeded_includes_details():
    with pytest.raises(ConnectionError) as exc_info:
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "operation timed out")
    assert "operation timed out" in str(exc_info.value)


def test_translate_grpc_exceptions_deadline_exceeded_with_empty_details():
    with pytest.raises(ConnectionError, match="deadline exceeded"):
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "")


def test_translate_grpc_exceptions_deadline_exceeded_includes_configuration_guidance():
    with pytest.raises(ConnectionError, match="Check timeout configuration"):
        with translate_grpc_exceptions():
            raise MockAioRpcError(StatusCode.DEADLINE_EXCEEDED, "operation timed out")
