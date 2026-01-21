import logging
import time
from typing import ClassVar

import pytest

from jumpstarter.common.utils import env
from jumpstarter.config.client import ClientConfigV1Alpha1

log = logging.getLogger(__name__)


class JumpstarterTest:
    """Base class for Jumpstarter test cases in pytest

    This class provides a client fixture that can be used to interact with
    Jumpstarter services in test cases.

    Looks for the `JUMPSTARTER_HOST` environment variable to connect to an
    established Jumpstarter shell, otherwise it will try to acquire a lease
    for a single exporter using the selector annotation.
    i.e.:

    .. code-block:: python

        import os
        import pytest
        import logging

        from jumpstarter_testing.pytest import JumpstarterTest

        log = logging.getLogger(__name__)

        class TestResource(JumpstarterTest):
            selector = "board=rpi4"

            @pytest.fixture()
            def console(self, client):
                with PexpectAdapter(client=client.dutlink.console) as console:
                    yield console

            def test_setup_device(self, client, console):
                client.dutlink.power.off()
                log.info("Setting up device")
                client.dutlink.storage.write_local_file("2024-07-04-raspios-bookworm-arm64-lite.img")
                client.dutlink.storage.dut()
                client.dutlink.power.on()

    """

    selector: ClassVar[str]

    @pytest.fixture(scope="class")
    def client(self):
        try:
            with env() as client:
                yield client
        except RuntimeError:
            selector = getattr(self, "selector", None)
            config = ClientConfigV1Alpha1.load("default")
            with config.lease(selector=selector) as lease:
                with lease.connect() as client:
                    yield client
        # BUG workaround: make sure that grpc servers get the client/lease release properly
        time.sleep(1)
