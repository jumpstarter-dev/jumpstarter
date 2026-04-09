from unittest.mock import patch

import pytest
from jumpstarter_driver_xcp.driver import Xcp

from .mock_ecu import MockXcpEcu
from jumpstarter.common.utils import serve


@pytest.fixture
def mock_ecu():
    """Provide a fresh stateful mock XCP ECU."""
    return MockXcpEcu()


@pytest.fixture
def ecu_client(mock_ecu):
    """XCP driver connected to the mock ECU via the jumpstarter harness."""
    driver = Xcp(
        transport="ETH",
        host="127.0.0.1",
        port=5555,
        protocol="TCP",
    )
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_ecu,
    ):
        with serve(driver) as client:
            yield client
