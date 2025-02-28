from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from secrets import randbits
from subprocess import PIPE, Popen, TimeoutExpired
from tempfile import TemporaryDirectory

import yaml
from jumpstarter_driver_network.driver import UnixNetwork, VsockNetwork
from jumpstarter_driver_opendal.driver import Opendal
from jumpstarter_driver_pyserial.driver import PySerial
from pydantic import validate_call

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Qemu(Driver):
    smp: int = 2
    mem: str = "512M"

    hostname: str = "demo"
    username: str = "jumpstarter"
    password: str = "password"

    root_dir: str = "/var/qemu"
    image: str = "default.qcow2"

    _tmp_dir: TemporaryDirectory = field(init=False, default_factory=TemporaryDirectory)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qemu.client.QemuClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.children["storage"] = Opendal(scheme="fs", kwargs={"root": self.root_dir})
        self.children["console"] = PySerial(url=self._pty, check_present=False)
        self.children["vnc"] = UnixNetwork(path=self._vnc)
        self.children["ssh"] = VsockNetwork(cid=self._cid, port=22)

    @property
    def _pty(self) -> str:
        return str(Path(self._tmp_dir.name) / "pty")

    @property
    def _vnc(self) -> str:
        return str(Path(self._tmp_dir.name) / "vnc")

    @cached_property
    def _cid(self) -> int:
        return randbits(32)

    @export
    @validate_call(validate_return=True)
    def set_image(self, path: str) -> None:
        self.image = path

    @export
    @validate_call(validate_return=True)
    def get_image(self) -> str:
        return self.image

    @export
    @validate_call(validate_return=True)
    def get_hostname(self) -> str:
        return self.hostname

    @export
    @validate_call(validate_return=True)
    def get_username(self) -> str:
        return self.username

    @export
    @validate_call(validate_return=True)
    def get_password(self) -> str:
        return self.password

    @export
    def start(self):
        root_dir = Path(self.root_dir)
        img_path = root_dir.joinpath(self.image)
        if not img_path.is_relative_to(root_dir):
            raise ValueError("path traversal")

        cidata = Path(self._tmp_dir.name) / "cidata"
        cidata.mkdir(exist_ok=True)

        (cidata / "meta-data").write_text(
            yaml.safe_dump(
                {
                    "instance-id": str(self.uuid),
                    "local-hostname": self.hostname,
                }
            )
        )

        (cidata / "user-data").write_text(
            "#cloud-config\n"
            + yaml.safe_dump(
                {
                    "ssh_pwauth": True,
                    "users": [
                        {
                            "name": self.username,
                            "plain_text_passwd": self.password,
                            "lock_passwd": False,
                            "sudo": "ALL=(ALL) NOPASSWD:ALL",
                        }
                    ],
                }
            )
        )

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
            "-blockdev",
            f"driver=vvfat,node-name=cidata,read-only=on,dir={cidata},label=CIDATA",
            "-device",
            "virtio-blk-pci,drive=cidata",
            "-serial",
            f"pty:{self._pty}",
            "-vnc",
            f"unix:{self._vnc}",
            "-device",
            f"vhost-vsock-pci,guest-cid={self._cid}",
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
