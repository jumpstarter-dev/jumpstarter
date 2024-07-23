import pytest

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.composite import Composite
from jumpstarter.drivers.power import MockPower

pytestmark = pytest.mark.anyio


async def test_drivers_composite():
    async with serve(
        Composite(
            labels={"jumpstarter.dev/name": "composite0"},
            childs=[
                MockPower(labels={"jumpstarter.dev/name": "power0"}),
                Composite(
                    labels={"jumpstarter.dev/name": "composite1"},
                    childs=[
                        MockPower(labels={"jumpstarter.dev/name": "power1"}),
                    ],
                ),
            ],
        )
    ) as client:
        assert await client.power0.on() == "ok"
        assert await client.composite1.power1.on() == "ok"
