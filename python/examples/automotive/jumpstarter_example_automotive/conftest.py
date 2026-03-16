import pytest
from jumpstarter_driver_uds_doip.driver import UdsDoip

from .mock_ecu import MockDiagnosticEcu
from jumpstarter.common.utils import serve


@pytest.fixture
def mock_ecu():
    """Start a stateful mock ECU on a dynamic port."""
    ecu = MockDiagnosticEcu()
    try:
        yield ecu
    finally:
        ecu.stop()


@pytest.fixture
def ecu_client(mock_ecu):
    """UDS-DoIP driver connected to the mock ECU via the jumpstarter harness."""
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_ecu.port,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        yield client
