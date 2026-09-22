"""Shared test fixtures for jumpstarter-driver-sigrok."""

import pytest

from .driver import Sigrok
from jumpstarter.common.utils import serve


@pytest.fixture
def demo_driver_instance():
    """Create a Sigrok driver instance configured for the demo device."""
    # Demo driver has 8 digital channels (D0-D7) and 5 analog (A0-A4)
    # Map device channels to decoder-friendly semantic names
    return Sigrok(
        driver="demo",
        channels={
            "D0": "vcc",
            "D1": "cs",
            "D2": "miso",
            "D3": "mosi",
            "D4": "clk",
            "D5": "sda",
            "D6": "scl",
            "D7": "gnd",
        },
    )


@pytest.fixture
def demo_client(demo_driver_instance):
    """Create a client connected to demo driver via serve()."""
    with serve(demo_driver_instance) as client:
        yield client
