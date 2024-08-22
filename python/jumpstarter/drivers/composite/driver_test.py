from jumpstarter.common.utils import serve
from jumpstarter.drivers.composite.driver import Composite
from jumpstarter.drivers.power.driver import MockPower


def test_drivers_composite():
    with serve(
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
        assert client.power0.on() == "ok"
        assert client.composite1.power1.on() == "ok"
