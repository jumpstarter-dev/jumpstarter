from jumpstarter.common.grpc import _override_default_grpc_options


def test_default_options_preserve_existing_defaults():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.lb_policy_name"] == "round_robin"
    assert options["grpc.keepalive_time_ms"] == 20000



def test_user_options_override_defaults():
    user_options = {"grpc.keepalive_time_ms": 50000}
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.keepalive_time_ms"] == 50000
