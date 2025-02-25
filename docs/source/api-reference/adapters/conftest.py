import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import EchoNetwork

from jumpstarter.common.utils import serve


@pytest.fixture
def network():
    with serve(Composite(children={"tcp_port": EchoNetwork(), "unix_socket": EchoNetwork()})) as client:
        yield client


@pytest.fixture(autouse=True)
def adapters_namespace(doctest_namespace, network):
    doctest_namespace["network"] = network
