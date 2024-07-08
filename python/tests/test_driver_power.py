from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.drivers.power.base import PowerReading
from jumpstarter.drivers.power.dutlink import DutlinkPower
from subprocess import run
from shutil import which
import pytest


def test_mock_power():
    p = MockPower(labels={"jumpstarter.dev/name": "mock"})
    assert p.call("on", [])
    assert p.call("off", [])
    assert p.call("read", []) == PowerReading(5.0, 2.0)


def check_dutlink():
    if which("jumpstarter") is None:
        return False
    if "test-device" not in str(
        run(["jumpstarter", "list-devices"], capture_output=True).stdout
    ):
        return False
    return True


@pytest.mark.skipif(not check_dutlink(), reason="no dutlink")
def test_dutlink_power():
    p = DutlinkPower(labels={"jumpstarter.dev/name": "dutlink"}, name="test-device")
    p.call("on", [])
    p.call("off", [])
