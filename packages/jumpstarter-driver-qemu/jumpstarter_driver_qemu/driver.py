from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired

from jumpstarter_driver_opendal.driver import Opendal
from jumpstarter_driver_pyserial.driver import PySerial
from pydantic import validate_call

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Qemu(Driver):
    smp: int = 2
    mem: str = "512M"

    root_dir: str = "/var/qemu"
    image: str = "default.qcow2"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qemu.client.QemuClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.root_dir})
        self.children["console"] = PySerial(url="socket://127.0.0.1:4444", check_present=False)

    @export
    @validate_call(validate_return=True)
    def set_image(self, path: str) -> None:
        self.image = path

    @export
    @validate_call(validate_return=True)
    def get_image(self) -> str:
        return self.image

    @export
    def start(self):
        root_dir = Path(self.root_dir)
        img_path = root_dir.joinpath(self.image)
        if not img_path.is_relative_to(root_dir):
            raise ValueError("path traversal")

        cmdline = [
            "qemu-system-x86_64",
            "-nographic",
            "-accel",
            "kvm",
            "-smp",
            str(self.smp),
            "-m",
            self.mem,
            "-hda",
            str(img_path),
            "-serial",
            "tcp:127.0.0.1:4444,server=on,wait=off",
        ]

        self._process = Popen(cmdline, stdin=PIPE, stdout=PIPE, stderr=PIPE)

    @export
    def stop(self):
        if hasattr(self, "_process"):
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except TimeoutExpired:
                self._process.kill()
            del self._process

    def close(self):
        self.stop()
