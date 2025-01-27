from jumpstarter_driver_power.driver import MockPower

from .driver import Composite
from jumpstarter.common.utils import serve


def test_drivers_composite():
    with serve(
        Composite(
            children={
                "power0": MockPower(),
                "composite1": Composite(
                    children={
                        "power1": MockPower(),
                    },
                ),
            },
        )
    ) as client:
        assert client.power0.on() == "ok"
        assert client.composite1.power1.on() == "ok"
