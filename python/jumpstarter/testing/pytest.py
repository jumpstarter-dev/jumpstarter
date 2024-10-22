import time
from typing import ClassVar

import pytest

from jumpstarter.common import MetadataFilter
from jumpstarter.common.utils import env
from jumpstarter.config.client import ClientConfigV1Alpha1


class JumpstarterTest:
    """
    Base class for Jumpstarter test cases in pytest

    This class provides a client fixture that can be used to interact with
    Jumpstarter services in test cases.

    Looks for the JUMPSTARTER_HOST environment variable to connect to a
    established Jumpstarter shell, otherwise it will try to acquire a lease
    for a single exporter using the filter_labels annotation.
    i.e.:
    ```
    class TestResource(JumpstarterTest):
        filter_labels = {"board":"rpi4"}

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
    ```
    """

    filter_labels: ClassVar[dict[str, str]]

    @pytest.fixture(scope="class")
    def client(self):
        try:
            with env() as client:
                yield client
        except RuntimeError:
            labels = getattr(self, "filter_labels", {})
            client = ClientConfigV1Alpha1.load("default")
            with client.lease(metadata_filter=MetadataFilter(labels=labels), lease_name=None) as lease:
                yield lease.connect()
        # BUG workaround: make sure that grpc servers get the client/lease release properly
        time.sleep(1)
