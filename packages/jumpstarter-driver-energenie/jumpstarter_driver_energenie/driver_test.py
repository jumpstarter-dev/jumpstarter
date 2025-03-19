from jumpstarter.common.utils import serve

from .driver import EnerGenie

def test_drivers_energenie():
    instance = EnerGenie()

    with serve(instance) as client:
        client.on()
        client.off()
