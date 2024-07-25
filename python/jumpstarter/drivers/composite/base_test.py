import pytest
from anyio.to_thread import run_sync

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.composite import Composite
from jumpstarter.drivers.power import MockPower

pytestmark = pytest.mark.anyio


async def test_drivers_composite():
    async with serve(
        Composite(
            name="composite0",
            children=[
                MockPower(name="power0"),
                Composite(
                    name="composite1",
                    children=[
                        MockPower(name="power1"),
                    ],
                ),
            ],
        )
    ) as client:

        def blocking():
            assert client.power0.on() == "ok"
            assert client.composite1.power1.on() == "ok"

        await run_sync(blocking)
