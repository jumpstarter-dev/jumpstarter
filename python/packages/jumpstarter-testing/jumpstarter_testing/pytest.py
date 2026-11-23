import logging
import os
import time
from typing import ClassVar

import pytest

from jumpstarter.client.lease import LeaseMembers
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.common.utils import env, env_members
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import JMP_LEASE_MEMBERS

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

    A test for more than one exporter sets `members` to a `LeaseMembers`
    subclass in the place of `selector`. The client fixture is then an
    instance of that class: in a `jmp shell` on a lease with members it
    attaches to the lease of the shell, otherwise it requests the lease
    template of the class.

    .. code-block:: python

        class TestProjection(JumpstarterTest):
            members = Projection

            def test_power(self, client: Projection) -> None:
                client.headunit.power.on()

    """

    selector: ClassVar[str]
    members: ClassVar[type[LeaseMembers]]

    # Declared as a classmethod because the fixture is class scoped: pytest
    # builds a fresh instance for every test but runs the fixture once, so an
    # instance method here would be operating on an object the tests never
    # see. Instance-method fixtures at class scope are deprecated and are
    # removed in pytest 10.
    @pytest.fixture(scope="class")
    @classmethod
    def client(cls):
        members = getattr(cls, "members", None)
        if members is not None:
            if getattr(cls, "selector", None) is not None:
                raise TypeError(f"{cls.__name__} sets both `members` and `selector`: a test class uses one of them")
            if os.environ.get(JMP_LEASE_MEMBERS):
                with env_members(members) as client:
                    yield client
            else:
                config = ClientConfigV1Alpha1.load("default")
                with config.lease(members) as lease, lease.connect() as client:
                    yield client
            time.sleep(1)
            return
        try:
            with env() as client:
                yield client
        # Outside a `jmp shell` there is no JUMPSTARTER_HOST, which is what
        # sends us down the lease path. RuntimeError stays in the tuple
        # because it was the only thing caught here before
        # EnvironmentVariableNotSetError existed.
        except (EnvironmentVariableNotSetError, RuntimeError):
            # A shell on a lease with members has no single exporter for a
            # selector test; the error says to use `members`.
            if os.environ.get(JMP_LEASE_MEMBERS):
                raise
            selector = getattr(cls, "selector", None)
            config = ClientConfigV1Alpha1.load("default")
            with config.lease(selector=selector) as lease, lease.connect() as client:
                yield client
        # BUG workaround: make sure that grpc servers get the client/lease release properly
        time.sleep(1)
