import time

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
    @classmethod
    def setup_class(cls):
        try:
            cls.__client = env()
            cls._client = cls.__client.__enter__()
        except RuntimeError:
            labels = getattr(cls, "filter_labels", {})
            cls._lease = ClientConfigV1Alpha1.load("default").lease(metadata_filter=MetadataFilter(labels=labels))
            cls.__client = cls._lease.__enter__().connect()
            cls._client = cls.__client.__enter__()

    @classmethod
    def teardown_class(cls):
        cls.__client.__exit__(None, None, None)
        if hasattr(cls, "_lease"):
            cls._lease.__exit__(None, None, None)

            # BUG workaround: make sure that grpc servers get the client/lease release properly
            time.sleep(1)

    @pytest.fixture()
    def client(self):
        return self._client

