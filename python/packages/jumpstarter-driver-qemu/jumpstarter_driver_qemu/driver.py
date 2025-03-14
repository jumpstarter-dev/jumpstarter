from __future__ import annotations

import json
import os
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
    async def on(self) -> None:  # noqa: C901
        root = self.parent.validate_partition("root")
        bios = self.parent.validate_partition("bios")
        ovmf_code = self.parent.validate_partition("OVMF_CODE.fd")
        ovmf_vars = self.parent.validate_partition("OVMF_VARS.fd")

        cpu = self.parent.cpu

        cmdline = [
            f"qemu-system-{self.parent.arch}",
            "-nodefaults",
            "-nographic",
        ]

        if self.parent.arch == platform.machine() and os.access("/dev/kvm", os.R_OK | os.W_OK):
            cmdline += [
                "-accel",
                "kvm",
            ]

            if cpu is None:
                cpu = "host"

        match self.parent.arch:
            case "aarch64":
                cmdline += ["-machine", "virt"]
            case "x86_64":
                cmdline += ["-machine", "q35"]

        if cpu is None:
            match self.parent.arch:
                case "aarch64":
                    cpu = "cortex-a57"
                case "x86_64":
                    cpu = "qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt"

        cmdline += [
            "-cpu",
            cpu,
            "-qmp",
            f"unix:{self.parent._qmp},server=on,wait=off",
            "-smp",
            str(self.parent.smp),
            "-m",
            self.parent.mem,
            "-vnc",
            f"unix:{self.parent._vnc}",
            "-vga",
            "none",
            "-serial",
            "pty",
            "-netdev",
            "user,id=eth0",
        ]

        devices = [
            "virtio-net-pci,netdev=eth0",
            "virtio-gpu-pci",
            "vhost-vsock-pci,guest-cid={}".format(self.parent._cid),
        ]
        for device in devices:
            cmdline += ["-device", device]

        if bios.exists():
            cmdline += [
                "-bios",
                str(bios),
            ]

        if ovmf_code.exists() and ovmf_vars.exists():
            cmdline += [
                "-drive",
                f"file={ovmf_code},if=pflash,format=raw,unit=0,readonly=on",
                "-drive",
                f"file={ovmf_vars},if=pflash,format=raw,unit=1,snapshot=on,readonly=off",
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

            cmdline += [
                "-blockdev",
                f"driver={image_driver},node-name=rootfs,file.driver=file,file.filename={root}",
                "-device",
                "virtio-blk-pci,drive=rootfs,bootindex=1",
            ]

        self._cidata = self.parent.cidata()

        cmdline += [
            "-blockdev",
            f"driver=vvfat,node-name=cidata,read-only=on,dir={self._cidata.name},label=CIDATA",
            "-device",
            "virtio-blk-pci,drive=cidata",
        ]

        self._process = Popen(cmdline, stdin=PIPE)

        qmp = QMPClient(self.parent.hostname)

        with fail_after(10):
            while qmp.runstate != Runstate.RUNNING:
                try:
                    await qmp.connect(self.parent._qmp)
                except ConnectError:
                    await sleep(0.5)

        chardevs = await qmp.execute("query-chardev")
        pty = next(c for c in chardevs if c["label"] == "serial0")["filename"].lstrip("pty:")
        Path(self.parent._pty).symlink_to(pty)

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
    arch: str = field(default_factory=platform.machine)
    cpu: str | None = None

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
            case "bios":
                return Path(self._tmp_dir.name) / "bios"
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
