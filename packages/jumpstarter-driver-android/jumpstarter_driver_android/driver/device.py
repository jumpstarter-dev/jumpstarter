from dataclasses import field

from pydantic.dataclasses import dataclass

from jumpstarter_driver_android.driver.adb import AdbServer
from jumpstarter_driver_android.driver.options import AdbOptions
from jumpstarter_driver_android.driver.scrcpy import Scrcpy

from jumpstarter.driver.base import Driver


@dataclass(kw_only=True)
class AndroidDevice(Driver):
    """
    A base Android device driver composed of the `AdbServer` and `Scrcpy` drivers.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_android.client.AndroidClient"

    adb: AdbOptions = field(default_factory=AdbOptions)
    disable_scrcpy: bool = field(default=False)
    disable_adb: bool = field(default=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if not self.disable_adb:
            self.children["adb"] = AdbServer(
                host=self.adb.host, port=self.adb.port, adb_path=self.adb.adb_path, log_level=self.log_level
            )
        if not self.disable_scrcpy:
            self.children["scrcpy"] = Scrcpy(host=self.adb.host, port=self.adb.port, log_level=self.log_level)
