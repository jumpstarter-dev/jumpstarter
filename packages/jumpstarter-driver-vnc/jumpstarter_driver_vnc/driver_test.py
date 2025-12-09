from __future__ import annotations

import pytest
from jumpstarter_driver_composite.client import CompositeClient

from jumpstarter_driver_vnc.driver import Vnc

from jumpstarter.client import DriverClient
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


class FakeTcpDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


def test_vnc_client_is_composite():
    """Test that the Vnc driver produces a composite client."""
    instance = Vnc(
        children={"tcp": FakeTcpDriver()},
    )

    with serve(instance) as client:
        assert isinstance(client, CompositeClient)
        assert isinstance(client.tcp, DriverClient)


def test_vnc_driver_raises_error_without_tcp_child():
    """Test that the Vnc driver raises a ConfigurationError if the tcp child is missing."""
    with pytest.raises(ConfigurationError, match="A tcp child is required for Vnc"):
        Vnc(children={})
