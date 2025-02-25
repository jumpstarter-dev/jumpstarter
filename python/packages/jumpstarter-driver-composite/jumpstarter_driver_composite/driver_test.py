from jumpstarter_driver_power.driver import MockPower

from .driver import Composite, Proxy
from jumpstarter.common.utils import serve


def test_drivers_composite():
    with serve(
        Composite(
            children={
                "proxy0": Proxy(ref="composite1.power1"),
                "proxy1": Proxy(ref="composite1"),
                "power0": MockPower(),
                "composite1": Composite(
                    children={
                        "power1": MockPower(),
                    },
                ),
            },
        )
    ) as client:
        client.power0.on()
        client.composite1.power1.on()
        client.proxy0.on()
        client.proxy1.power1.on()
