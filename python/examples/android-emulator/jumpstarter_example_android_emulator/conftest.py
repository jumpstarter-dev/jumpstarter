import os

import pytest
from jumpstarter_driver_androidemulator.driver import AndroidEmulator

from jumpstarter.common.utils import serve


@pytest.fixture(scope="session")
def emulator_client():
    """Boot an Android emulator and yield a Jumpstarter client.

    The emulator runs for the entire test session and is shut down
    at the end. Set ANDROID_AVD_NAME to specify which AVD to use.
    """
    avd_name = os.environ.get("ANDROID_AVD_NAME", "jumpstarter_test")
    driver = AndroidEmulator(avd_name=avd_name, headless=False)

    with serve(driver) as client:
        client.power.on()
        yield client
        client.power.off()


@pytest.fixture(scope="session")
def adb_device(emulator_client):
    """Yield an adbutils device connected through the ADB tunnel.

    Waits for the emulator to finish booting before yielding.
    """
    with emulator_client.adb_device(timeout=180) as device:
        yield device
