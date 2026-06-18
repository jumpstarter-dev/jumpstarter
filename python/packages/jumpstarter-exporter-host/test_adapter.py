"""Standalone test of the in-process adapter against a real mock Driver (what Rust calls).

Run: PYTHONPATH=<ext-dir>:. python/.venv/bin/python test_adapter.py
"""
import asyncio
import json
import math
import sys

import jumpstarter_core as jc
from jumpstarter.driver import Driver, export

from jumpstarter_exporter_host._adapter import DriverHost, _to_jsonable


class MockPower(Driver):
    @classmethod
    def client(cls):
        return "mock.PowerClient"

    @export
    def on(self):  # sync unary
        return "on"

    @export
    async def voltage(self):  # async unary, returns a dict with an int (-> f64 on the wire)
        return {"v": 3.3, "n": 42}

    @export
    def boom(self):
        raise NotImplementedError("nope")

    @export
    def bad(self):
        raise ValueError("bad arg")

    @export
    async def count(self):  # streaming (async generator)
        for i in range(3):
            yield i


async def main():
    # _to_jsonable quirks (match the Rust value golden corpus)
    assert _to_jsonable(math.nan) is None
    assert _to_jsonable(math.inf) is None
    assert _to_jsonable(b"hi") == "hi"
    assert _to_jsonable((1, 2, 3)) == [1, 2, 3]
    assert _to_jsonable({"a": (1, b"x")}) == {"a": [1, "x"]}

    root = MockPower()
    host = DriverHost(root)
    uuid = str(root.uuid)

    nodes = await host.describe()
    assert len(nodes) == 1, nodes
    assert nodes[0].uuid == uuid
    assert nodes[0].labels["jumpstarter.dev/client"] == "mock.PowerClient"

    assert await host.driver_call(uuid, "on", "[]") == '"on"'
    assert json.loads(await host.driver_call(uuid, "voltage", "[]")) == {"v": 3.3, "n": 42}

    for method, err in [("boom", jc.DriverError.Unimplemented), ("bad", jc.DriverError.InvalidArgument)]:
        try:
            await host.driver_call(uuid, method, "[]")
            raise AssertionError(f"{method} should have raised")
        except jc.DriverError as e:
            assert isinstance(e, err), f"{method} -> {type(e).__name__}"

    # unknown uuid / method
    try:
        await host.driver_call("nope", "on", "[]")
        raise AssertionError("unknown uuid should raise")
    except jc.DriverError.NotFound:
        pass

    # streaming
    handle = await host.streaming_open(uuid, "count", "[]")
    results = []
    while True:
        item = await host.streaming_next(handle)
        if item is None:
            break
        results.append(json.loads(item))
    assert results == [0, 1, 2], results
    await host.streaming_close(handle)

    print("PASS: _to_jsonable quirks, describe, driver_call, error mapping, unknown-uuid, streaming")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        raise
