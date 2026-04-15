import time
from contextlib import contextmanager

from jumpstarter_driver_composite.client import CompositeClient


class AndroidEmulatorClient(CompositeClient):
    """Client for Android emulator with ADB access.

    Children:
    - ``adb``: ADB client for device communication
    - ``power``: Power client for emulator lifecycle
    """

    def set_headless(self, headless: bool) -> None:
        """Set headless mode. Must be called before power on."""
        self.call("set_headless", headless)

    @contextmanager
    def adb_device(self, timeout: int = 180):
        """Forward ADB, wait for boot, and yield an adbutils device.

        Requires the ``python-api`` extra (``adbutils``).

        Args:
            timeout: Seconds to wait for the emulator to finish booting.

        Yields:
            An ``adbutils.AdbDevice`` connected through the tunnel.
        """
        import adbutils  # ty: ignore[unresolved-import]

        with self.adb.forward_adb(port=0) as (host, port):
            adb = adbutils.AdbClient(host=host, port=port)
            self._wait_for_boot(adb, timeout)
            devices = adb.device_list()
            if not devices:
                raise RuntimeError("No devices found after boot wait")
            yield devices[0]

    def _wait_for_boot(self, adb, timeout: int = 180) -> None:
        """Poll until the emulator reports boot complete."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                devices = adb.device_list()
                if devices:
                    result = devices[0].shell("getprop sys.boot_completed").strip()
                    if result == "1":
                        return
            except Exception:
                pass
            time.sleep(2)
        raise TimeoutError(f"Emulator did not boot within {timeout} seconds")

    def cli(self):
        return super().cli()
