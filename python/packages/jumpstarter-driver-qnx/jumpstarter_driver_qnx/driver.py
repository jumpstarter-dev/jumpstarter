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

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QnxPowerClient"


@dataclass(kw_only=True)
class Qnx(Driver):
    """QNX board driver for Jumpstarter.

    Composite driver that manages a physical QNX board: hard power
    (via external power_ctl child), IPL boot menu interaction,
    serial console, and TCP/SSH access.
    """

    driver_type = "composite"

    # IPL boot menu configuration
    boot_prompt: str = "Press 'M' for eSD download"
    boot_key: str = "M"
    final_prompt: str = "#"

    # Optional shell scriptlet to run after QNX boot
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

        self.children["power"] = QnxPower()

    @export
    def get_boot_prompt(self) -> str:
        return self.boot_prompt

    @export
    def get_boot_key(self) -> str:
        return self.boot_key

    @export
    def get_final_prompt(self) -> str:
        return self.final_prompt

    @export
    def get_init_script(self) -> str:
        return self.init_script

    @export
    def get_boot_timeout(self) -> int:
        return self.boot_timeout

    @export
    def get_prompt_timeout(self) -> int:
        return self.prompt_timeout


@dataclass(kw_only=True)
class QvmPower(Driver):
    """QVM power driver — launches/stops QVM via the QNX serial console.

    Auto-created by the Qvm driver.
    """

    driver_type = "power"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QvmPowerClient"


@dataclass(kw_only=True)
class QvmFlasher(Driver):
    """QVM flasher driver — runs fls with ssh:// target.

    Auto-created by the Qvm driver.
    """

    driver_type = "storage"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.client.QvmFlasherClient"


@dataclass(kw_only=True)
class Qvm(Driver):
    """QNX Virtual Machine driver for Jumpstarter.

    Composite driver that manages a QVM instance running inside QNX.
    Requires a 'qnx' child (via ref) pointing to a Qnx driver.
    """

    driver_type = "composite"

    # Block device for flashing
    emmc_device: str = "/dev/emmc0"

    # SSH password for fls ssh:// target (optional)
    ssh_password: str = ""

    # QVM binary and launch configuration
    qvm_path: str = "/sbin/qvm"
    working_directory: str = "/tmp"
    qvm_config_path: str = "/tmp/qvm.conf"

    # virtio-net MAC address
    virtio_net_mac: str = ""

    # Optional U-Boot bootcmd
    uboot_bootcmd: str = ""

    # Default QVM config template (supports {variable} substitution)
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

        self.children["power"] = QvmPower()
        self.children["storage"] = QvmFlasher()

    def render_config(self) -> str:
        """Render the QVM config template with variable substitution."""
        if not self.qvm_default_config:
            return ""
        return self.qvm_default_config.format(
            virtio_net_mac=self.virtio_net_mac,
            emmc_device=self.emmc_device,
        )

    @export
    def get_emmc_device(self) -> str:
        return self.emmc_device

    @export
    def get_ssh_password(self) -> str:
        return self.ssh_password

    @export
    def get_qvm_path(self) -> str:
        return self.qvm_path

    @export
    def get_working_directory(self) -> str:
        return self.working_directory

    @export
    def get_qvm_config_path(self) -> str:
        return self.qvm_config_path

    @export
    def get_virtio_net_mac(self) -> str:
        return self.virtio_net_mac

    @export
    def get_uboot_bootcmd(self) -> str:
        return self.uboot_bootcmd

    @export
    def get_qvm_default_config(self) -> str:
        return self.qvm_default_config

    @export
    def get_rendered_config(self) -> str:
        return self.render_config()

    @export
    def get_qvm_start_timeout(self) -> int:
        return self.qvm_start_timeout

    @export
    def get_flash_timeout(self) -> int:
        return self.flash_timeout
