import anyio
import pytest

from jumpstarter_mcp import telemetry

otel_context = pytest.importorskip("opentelemetry.context")


def test_traces_are_only_sent_when_a_person_asked_the_standard_way(monkeypatch):
    for name in (*telemetry.ASKED, "OTEL_SDK_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    assert telemetry.asked() is False and telemetry.setup() is False
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    assert telemetry.asked() is True
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert telemetry.asked() is False
    monkeypatch.delenv("OTEL_SDK_DISABLED")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    assert telemetry.asked() is False


@pytest.mark.anyio
async def test_a_trace_is_carried_to_the_task_that_passes_a_call_on():
    key = otel_context.create_key("where-the-agent-asked")
    seen = []

    async def elsewhere(carried):
        seen.append(otel_context.get_value(key))  # another task: not in the caller's trace
        with telemetry.within(carried):
            seen.append(otel_context.get_value(key))
        seen.append(otel_context.get_value(key))

    token = otel_context.attach(otel_context.set_value(key, "trace-1"))
    try:
        carried = telemetry.current()
    finally:
        otel_context.detach(token)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(elsewhere, carried)
    assert seen == [None, "trace-1", None]


def test_saying_what_a_call_is_about_costs_nothing_when_nothing_is_recorded():
    telemetry.annotate(tool="power_on", connection__id="abc", exporter__name=None)
    with telemetry.within(None):
        pass
