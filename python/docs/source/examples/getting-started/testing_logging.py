import logging

import pytest
from jumpstarter_driver_network.adapters import PexpectAdapter
from jumpstarter_testing.pytest import JumpstarterTest

log = logging.getLogger(__name__)


class TestDiagnostics(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def console(self, client):
        with PexpectAdapter(client=client.dutlink.console) as console:
            yield console

    def test_firmware_version(self, client, console):
        client.dutlink.power.on()
        console.expect("version:", timeout=60)
        log.info("Firmware reported: %s", console.after)
        client.dutlink.power.off()
