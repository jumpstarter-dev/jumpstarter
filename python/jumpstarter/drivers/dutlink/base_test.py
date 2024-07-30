from time import sleep

from jumpstarter.common.utils import serve
from jumpstarter.drivers.dutlink.base import Dutlink


def test_drivers_dutlink():
    with serve(
        Dutlink(
            name="dutlink",
            storage_device="/dev/null",
        )
    ) as client:
        client.power.off()
        sleep(1)
        client.power.on()
        sleep(1)
        client.power.off()

        client.storage.host()
        client.storage.dut()
        client.storage.off()

        client.storage.write("/dev/null")

        with client.console.connect() as stream:
            pass
