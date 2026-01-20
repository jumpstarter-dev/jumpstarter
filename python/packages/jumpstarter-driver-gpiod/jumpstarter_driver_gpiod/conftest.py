import platform

import pytest

# Skip all tests on Darwin (macOS) due to missing gpiod module
pytestmark = pytest.mark.skipif(platform.system() == "Darwin", reason="gpiod module not available on Darwin/macOS")


# Add test utilities for GPIO testing
@pytest.fixture
def mock_gpiod_values():
    """Provide mock gpiod value constants for testing"""
    return {"ACTIVE": 1, "INACTIVE": 0, "RISING_EDGE": "rising", "FALLING_EDGE": "falling"}


@pytest.fixture
def mock_gpiod_directions():
    """Provide mock gpiod direction constants for testing"""
    return {"INPUT": "input", "OUTPUT": "output"}


@pytest.fixture
def mock_gpiod_drives():
    """Provide mock gpiod drive constants for testing"""
    return {"PUSH_PULL": "push_pull", "OPEN_DRAIN": "open_drain", "OPEN_SOURCE": "open_source"}


@pytest.fixture
def mock_gpiod_biases():
    """Provide mock gpiod bias constants for testing"""
    return {"AS_IS": "as_is", "PULL_UP": "pull_up", "PULL_DOWN": "pull_down", "DISABLED": "disabled"}
