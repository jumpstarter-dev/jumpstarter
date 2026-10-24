from dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class QnxPower(Driver):
    """Orchestrated power driver for QNX boards.

    Wraps a raw power_ctl child with IPL boot menu interaction
    and init script execution. Auto-created by the Qnx driver.
    """

    driver_type = "power"

    boot_prompt: str = "Press 'M' for eSD download"
    boot_key: str = "M"
    final_prompt: str = "#"
    init_script: str = ""
    boot_timeout: int = 60
    prompt_timeout: int = 120

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QnxPowerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for child in ("power_ctl", "serial"):
            if child not in self.children:
                raise ConfigurationError(
                    f"QnxPower requires '{child}' child driver"
                )

    @export
    def get_config(self) -> dict:
        """Return all QnxPower config in one RPC (avoids log spam from many get_* calls)."""
        return {
            "boot_prompt": self.boot_prompt,
            "boot_key": self.boot_key,
            "final_prompt": self.final_prompt,
            "init_script": self.init_script,
            "boot_timeout": self.boot_timeout,
            "prompt_timeout": self.prompt_timeout,
        }


@dataclass(kw_only=True)
class Qnx(Driver):
    """QNX board driver for Jumpstarter.

    Composite driver that manages a physical QNX board: hard power
    (via external power_ctl child), IPL boot menu interaction,
    serial console, and TCP/SSH access.
    """

    driver_type = "composite"

    boot_prompt: str = "Press 'M' for eSD download"
    boot_key: str = "M"
    final_prompt: str = "#"
    init_script: str = ""
    boot_timeout: int = 60
    prompt_timeout: int = 120

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QnxClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for child in ("power_ctl", "serial"):
            if child not in self.children:
                raise ConfigurationError(
                    f"Qnx driver requires '{child}' child driver"
                )

        # Share power_ctl/serial with the orchestrated power child
        # (same pattern as BaseFlasher → UbootConsole)
        self.children["power"] = QnxPower(
            boot_prompt=self.boot_prompt,
            boot_key=self.boot_key,
            final_prompt=self.final_prompt,
            init_script=self.init_script,
            boot_timeout=self.boot_timeout,
            prompt_timeout=self.prompt_timeout,
            children={
                "power_ctl": self.children["power_ctl"],
                "serial": self.children["serial"],
            },
        )

    @export
    def get_final_prompt(self) -> str:
        return self.final_prompt


@dataclass(kw_only=True)
class QvmPower(Driver):
    """QVM power driver — launches/stops QVM via the QNX serial console.

    Auto-created by the Qvm driver. Requires a 'qnx' child.
    """

    driver_type = "power"

    qvm_path: str = "/sbin/qvm"
    working_directory: str = "/tmp"
    qvm_config_path: str = "/tmp/qvm.conf"
    uboot_bootcmd: str = ""
    qvm_start_timeout: int = 30
    qvm_default_config: str = ""
    virtio_net_mac: str = ""
    emmc_device: str = "/dev/emmc0"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QvmPowerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "qnx" not in self.children:
            raise ConfigurationError("QvmPower requires 'qnx' child driver")

    def render_config(self) -> str:
        if not self.qvm_default_config:
            return ""
        return self.qvm_default_config.format(
            virtio_net_mac=self.virtio_net_mac,
            emmc_device=self.emmc_device,
        )

    @export
    def get_config(self) -> dict:
        """Return all QvmPower config in one RPC."""
        return {
            "qvm_path": self.qvm_path,
            "working_directory": self.working_directory,
            "qvm_config_path": self.qvm_config_path,
            "uboot_bootcmd": self.uboot_bootcmd,
            "qvm_start_timeout": self.qvm_start_timeout,
            "rendered_config": self.render_config(),
        }


@dataclass(kw_only=True)
class QvmFlasher(Driver):
    """QVM flasher driver — runs fls with ssh:// target.

    Auto-created by the Qvm driver. Requires a 'qnx' child.
    """

    driver_type = "storage"

    emmc_device: str = "/dev/emmc0"
    ssh_password: str = ""
    flash_timeout: int = 1800

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QvmFlasherClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "qnx" not in self.children:
            raise ConfigurationError("QvmFlasher requires 'qnx' child driver")

    @export
    def get_config(self) -> dict:
        """Return all QvmFlasher config in one RPC."""
        return {
            "emmc_device": self.emmc_device,
            "ssh_password": self.ssh_password,
            "flash_timeout": self.flash_timeout,
        }


@dataclass(kw_only=True)
class Qvm(Driver):
    """QNX Virtual Machine driver for Jumpstarter.

    Composite driver that manages a QVM instance running inside QNX.
    Requires a 'qnx' child (via ref) pointing to a Qnx driver.
    """

    driver_type = "composite"

    emmc_device: str = "/dev/emmc0"
    ssh_password: str = ""
    qvm_path: str = "/sbin/qvm"
    working_directory: str = "/tmp"
    qvm_config_path: str = "/tmp/qvm.conf"
    virtio_net_mac: str = ""
    uboot_bootcmd: str = ""
    qvm_default_config: str = ""
    qvm_start_timeout: int = 30
    flash_timeout: int = 1800

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QvmClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if "qnx" not in self.children:
            raise ConfigurationError(
                "Qvm driver requires 'qnx' child driver (use ref: to reference a Qnx driver)"
            )

        qnx = self.children["qnx"]
        self.children["power"] = QvmPower(
            qvm_path=self.qvm_path,
            working_directory=self.working_directory,
            qvm_config_path=self.qvm_config_path,
            uboot_bootcmd=self.uboot_bootcmd,
            qvm_start_timeout=self.qvm_start_timeout,
            qvm_default_config=self.qvm_default_config,
            virtio_net_mac=self.virtio_net_mac,
            emmc_device=self.emmc_device,
            children={"qnx": qnx},
        )
        self.children["storage"] = QvmFlasher(
            emmc_device=self.emmc_device,
            ssh_password=self.ssh_password,
            flash_timeout=self.flash_timeout,
            children={"qnx": qnx},
        )

    def render_config(self) -> str:
        """Render the QVM config template with variable substitution."""
        if not self.qvm_default_config:
            return ""
        return self.qvm_default_config.format(
            virtio_net_mac=self.virtio_net_mac,
            emmc_device=self.emmc_device,
        )

    @export
    def get_qvm_config_path(self) -> str:
        return self.qvm_config_path

    @export
    def get_rendered_config(self) -> str:
        return self.render_config()
