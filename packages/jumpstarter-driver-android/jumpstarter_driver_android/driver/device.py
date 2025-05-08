from dataclasses import dataclass
from typing import override

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
    @override
    def client(cls) -> str:
        return "jumpstarter_driver_android.client.AndroidClient"

    adb: AdbOptions
    disable_scrcpy: bool = False
    disable_adb: bool = False

    def __init__(self, **kwargs):
        self.adb = AdbOptions.model_validate(kwargs.get("adb", {}))
        if hasattr(super(), "__init__"):
            super().__init__(**kwargs)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if not self.disable_adb:
            self.children["adb"] = AdbServer(
                host=self.adb.host, port=self.adb.port, adb_path=self.adb.adb_path, log_level=self.log_level
            )
        if not self.disable_scrcpy:
            self.children["scrcpy"] = Scrcpy(host=self.adb.host, port=self.adb.port, log_level=self.log_level)
