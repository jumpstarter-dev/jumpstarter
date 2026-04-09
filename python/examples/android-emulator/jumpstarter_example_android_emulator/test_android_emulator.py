"""Android emulator integration tests using Jumpstarter ADB tunneling.

These tests demonstrate interacting with an Android device through
the Jumpstarter ADB tunnel using the adbutils Python API. No APK
is required — all tests use built-in Android capabilities.
"""

import os
import tempfile
import time


def test_device_properties(adb_device):
    """Read basic device properties via getprop."""
    model = adb_device.prop.model
    assert model is not None and len(model) > 0

    sdk = adb_device.prop.get("ro.build.version.sdk")
    assert sdk is not None
    assert int(sdk) > 0


def test_list_packages(adb_device):
    """Verify standard system packages are installed."""
    output = adb_device.shell("pm list packages")
    packages = output.strip().split("\n")
    assert len(packages) > 0
    assert any("com.android.settings" in p for p in packages)


def test_launch_settings(adb_device):
    """Launch the Settings app and verify it starts."""
    adb_device.shell("am start -a android.settings.SETTINGS")
    time.sleep(2)
    output = adb_device.shell("dumpsys activity activities")
    assert "settings" in output.lower()


def test_file_push_pull(adb_device):
    """Push a file to the device and pull it back."""
    test_content = b"jumpstarter-adb-test-content"
    remote_path = "/data/local/tmp/jumpstarter_test.txt"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(test_content)
        local_path = f.name

    pull_path = local_path + ".pulled"
    try:
        adb_device.sync.push(local_path, remote_path)

        result = adb_device.shell(f"cat {remote_path}")
        assert result.strip() == test_content.decode()

        adb_device.sync.pull(remote_path, pull_path)
        with open(pull_path, "rb") as f:
            assert f.read() == test_content

        adb_device.shell(f"rm {remote_path}")
    finally:
        os.unlink(local_path)
        if os.path.exists(pull_path):
            os.unlink(pull_path)


def test_display_size(adb_device):
    """Read the display size."""
    output = adb_device.shell("wm size")
    assert "x" in output


def test_battery_info(adb_device):
    """Read battery information from the emulator."""
    output = adb_device.shell("dumpsys battery")
    assert "level" in output.lower()
    assert "status" in output.lower()


def test_adb_list_devices(emulator_client):
    """Verify the exporter's ADB server sees the emulator."""
    output = emulator_client.adb.list_devices()
    assert "emulator" in output or "device" in output
