# Ported from https://github.com/kubernetes/apimachinery/blob/v0.31.1/pkg/api/meta/conditions.go

from jumpstarter_protocol import kubernetes_pb2


def condition_present_and_equal(
    conditions: list[kubernetes_pb2.Condition], condition_type: str, status: str, reason: str | None = None
) -> bool:
    for condition in conditions:
        if condition.type == condition_type:
            if reason is None or condition.reason == reason:
                return condition.status == status
    return False


def condition_message(
    conditions: list[kubernetes_pb2.Condition], condition_type: str, reason: str | None = None
) -> str | None:
    for condition in conditions:
        if condition.type == condition_type:
            if reason is None or condition.reason == reason:
                return condition.message
    return None


def condition_true(conditions: list[kubernetes_pb2.Condition], condition_type: str) -> bool:
    return condition_present_and_equal(conditions, condition_type, "True")


def condition_false(conditions: list[kubernetes_pb2.Condition], condition_type: str) -> bool:
    return condition_present_and_equal(conditions, condition_type, "False")
