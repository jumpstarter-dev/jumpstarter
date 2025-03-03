from __future__ import annotations

import json
import platform
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from secrets import randbits
from subprocess import PIPE, CalledProcessError, Popen, TimeoutExpired
from tempfile import TemporaryDirectory

import yaml
from anyio import fail_after, run_process, sleep
from anyio.streams.file import FileReadStream, FileWriteStream
from jumpstarter_driver_network.driver import UnixNetwork, VsockNetwork
from jumpstarter_driver_opendal.driver import FlasherInterface
from jumpstarter_driver_power.driver import PowerInterface, PowerReading
from jumpstarter_driver_pyserial.driver import PySerial
from pydantic import validate_call
from qemu.qmp import QMPClient
from qemu.qmp.protocol import ConnectError, Runstate

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class QemuFlasher(FlasherInterface, Driver):
    parent: Qemu

    @export
    async def flash(self, source, partition: str | None = None):
        async with await FileWriteStream.from_path(self.parent.validate_partition(partition)) as stream:
            async with self.resource(source) as res:
                async for chunk in res:
                    await stream.send(chunk)

    @export
    async def dump(self, target, partition: str | None = None):
        async with await FileReadStream.from_path(self.parent.validate_partition(partition)) as stream:
            async with self.resource(target) as res:
                async for chunk in stream:
                    await res.send(chunk)


@dataclass(kw_only=True)
class QemuPower(PowerInterface, Driver):
    parent: Qemu

    @export
    async def on(self) -> None:
        root = self.parent.validate_partition("root")
        ovmf_code = self.parent.validate_partition("OVMF_CODE.fd")
        ovmf_vars = self.parent.validate_partition("OVMF_VARS.fd")

        cmdline = [
            f"qemu-system-{self.parent.arch}",
            "-nographic",
            "-nodefaults",
            "-accel",
            "kvm",
            "-cpu",
            self.parent.cpu,
            "-qmp",
            f"unix:{self.parent._qmp},server=on,wait=off",
            "-smp",
            str(self.parent.smp),
            "-m",
            self.parent.mem,
            "-vnc",
            f"unix:{self.parent._vnc}",
            "-vga",
            "virtio",
            "-serial",
            "pty",
        ]

        if ovmf_code.exists() and ovmf_vars.exists():
            cmdline += [
                "-drive",
                f"file={ovmf_code},if=pflash,format=raw,unit=0,readonly=on",
                "-drive",
                f"file={ovmf_vars},if=pflash,format=raw,unit=1,snapshot=on,readonly=off",
            ]

        self._cidata = self.parent.cidata()
        self._process = Popen(cmdline, stdin=PIPE)

        qmp = QMPClient(self.parent.hostname)

        with fail_after(10):
            while qmp.runstate != Runstate.RUNNING:
                try:
                    await qmp.connect(self.parent._qmp)
                except ConnectError:
                    await sleep(0.5)

        pty = await qmp.execute(
            "chardev-change",
            {
                "id": "serial0",
                "backend": {
                    "type": "pty",
                    "data": {},
                },
            },
        )

        Path(self.parent._pty).symlink_to(pty["pty"])

        blockdevs = [
            {
                "driver": "vvfat",
                "node-name": "cidata",
                "read-only": True,
                "dir": self._cidata.name,
                "label": "CIDATA",
            },
        ]

        netdevs = [
            {
                "id": "eth0",
                "type": "user",
            }
        ]

        devices = [
            {
                "driver": "virtio-blk-pci",
                "drive": "cidata",
            },
            {
                "driver": "vhost-vsock-pci",
                "guest-cid": self.parent._cid,
            },
            {
                "driver": "virtio-net-pci",
                "netdev": "eth0",
            },
        ]

        if root.exists():
            proc = await run_process(
                ["qemu-img", "info", "--output=json", str(root)],
                stdout=PIPE,
                stderr=PIPE,
            )
            try:
                proc.check_returncode()
                info = json.loads(proc.stdout)
                image_format = info.get("format", "raw")
                match image_format:
                    case "raw" | "qcow2" | "qcow" | "vmdk":
                        image_driver = image_format
                    case _:
                        raise ValueError(f"unsupported image format: {image_format}")
            except CalledProcessError:
                self.logger.warning("unable to detect image format, assuming raw")
                image_driver = "raw"

            blockdevs.append(
                {
                    "driver": image_driver,
                    "node-name": "rootfs",
                    "file": {
                        "driver": "file",
                        "filename": str(root),
                    },
                }
            )
            devices.append(
                {
                    "driver": "virtio-blk-pci",
                    "drive": "rootfs",
                    "bootindex": 1,
                }
            )

        for blockdev in blockdevs:
            await qmp.execute("blockdev-add", blockdev)

        for netdev in netdevs:
            await qmp.execute("netdev_add", netdev)

        for device in devices:
            await qmp.execute("device_add", device)

        await qmp.execute("system_reset")

    @export
    def off(self) -> None:
        if hasattr(self, "_process"):
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except TimeoutExpired:
                self._process.kill()
            del self._process

        if hasattr(self, "_cidata"):
            del self._cidata

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        pass

    def close(self):
        self.off()


@dataclass(kw_only=True)
class Qemu(Driver):
    arch: str = field(default_factory=lambda: platform.uname().machine)
    cpu: str = "host"

    smp: int = 2
    mem: str = "512M"

    hostname: str = "demo"
    username: str = "jumpstarter"
    password: str = "password"

    _tmp_dir: TemporaryDirectory = field(init=False, default_factory=TemporaryDirectory)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qemu.client.QemuClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.children["power"] = QemuPower(parent=self)
        self.children["flasher"] = QemuFlasher(parent=self)
        self.children["console"] = PySerial(url=self._pty, check_present=False)
        self.children["vnc"] = UnixNetwork(path=self._vnc)
        self.children["ssh"] = VsockNetwork(cid=self._cid, port=22)

    @property
    def _pty(self) -> str:
        return str(Path(self._tmp_dir.name) / "pty")

    @property
    def _vnc(self) -> str:
        return str(Path(self._tmp_dir.name) / "vnc")

    @property
    def _qmp(self) -> str:
        return str(Path(self._tmp_dir.name) / "qmp")

    @cached_property
    def _cid(self) -> int:
        return randbits(32)

    def validate_partition(self, partition: str | None = None) -> Path:
        match partition:
            case "root" | None:
                return Path(self._tmp_dir.name) / "root"
            case "OVMF_CODE.fd":
                return Path(self._tmp_dir.name) / "OVMF_CODE.fd"
            case "OVMF_VARS.fd":
                return Path(self._tmp_dir.name) / "OVMF_VARS.fd"
            case _:
                raise ValueError(f"invalida partition name: {partition}")

    def cidata(self) -> TemporaryDirectory:
        tmp = TemporaryDirectory()

        path = Path(tmp.name)
        (path / "meta-data").write_text(
            yaml.safe_dump(
                {
                    "instance-id": str(self.uuid),
                    "local-hostname": self.hostname,
                }
            )
        )
        (path / "user-data").write_text(
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

        return tmp

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
