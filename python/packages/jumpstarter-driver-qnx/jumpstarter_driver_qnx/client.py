import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import cached_property
from urllib.parse import urlparse

import click
import pexpect
from jumpstarter_driver_composite.client import CompositeClient

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.common.fls import get_fls_binary


class QnxState(Enum):
    """State machine for QNX/QVM serial port ownership."""

    BOARD_OFF = auto()
    QNX_BOOTING = auto()
    QNX_READY = auto()
    QVM_STARTING = auto()
    QVM_RUNNING = auto()
    QVM_STOPPING = auto()


@dataclass(kw_only=True)
class QnxPowerClient(DriverClient):
    """Client for the orchestrated QNX power driver.

    Orchestrates: power_ctl.cycle() → IPL boot menu → init_script.
    """

    # State shared with QvmPowerClient via the parent QnxClient
    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    _state: QnxState = QnxState.BOARD_OFF
    _state_event: threading.Event = field(default_factory=threading.Event)

    @cached_property
    def _boot_prompt(self) -> str:
        return self.call("get_boot_prompt")

    @cached_property
    def _boot_key(self) -> str:
        return self.call("get_boot_key")

    @cached_property
    def _final_prompt(self) -> str:
        return self.call("get_final_prompt")

    @cached_property
    def _init_script(self) -> str:
        return self.call("get_init_script")

    @cached_property
    def _boot_timeout(self) -> int:
        return self.call("get_boot_timeout")

    @cached_property
    def _prompt_timeout(self) -> int:
        return self.call("get_prompt_timeout")

    def _set_state(self, state: QnxState):
        with self._state_lock:
            self._state = state
            if state == QnxState.QVM_RUNNING:
                self._state_event.set()
            else:
                self._state_event.clear()

    def on(self):
        """Power on QNX board with full boot orchestration."""
        self._set_state(QnxState.QNX_BOOTING)
        try:
            # Step 1: Power cycle to ensure known state
            self.logger.info("Power cycling board...")
            # Access power_ctl through the parent Qnx composite
            parent = self._parent_client
            parent.power_ctl.cycle()

            # Steps 2-3: IPL boot menu interaction
            if self._boot_prompt and self._boot_key:
                self.logger.info(f"Waiting for IPL boot prompt: {self._boot_prompt!r}")
                with parent.serial.pexpect() as console:
                    # Send space to trigger re-prompt if serial opened late
                    console.send(" ")
                    console.expect(
                        self._boot_prompt, timeout=self._boot_timeout
                    )
                    self.logger.info(f"Sending boot key: {self._boot_key!r}")
                    console.send(self._boot_key)

                    # Step 4: Wait for QNX shell prompt
                    self.logger.info(
                        f"Waiting for QNX prompt: {self._final_prompt!r}"
                    )
                    console.expect(
                        self._final_prompt, timeout=self._prompt_timeout
                    )

                    # Step 5-6: Run init script if configured
                    if self._init_script:
                        self.logger.info("Running init script...")
                        # Send the whole script as a block, then check exit code
                        script_block = self._init_script.rstrip("\n")
                        for line in script_block.splitlines():
                            console.sendline(line)
                        console.sendline("echo INIT_EXIT_CODE=$?")
                        console.expect(
                            r"INIT_EXIT_CODE=(\d+)",
                            timeout=self._prompt_timeout,
                        )
                        exit_code = int(console.match.group(1))
                        if exit_code != 0:
                            raise RuntimeError(
                                f"Init script failed with exit code {exit_code}"
                            )
                        console.expect(
                            self._final_prompt,
                            timeout=self._prompt_timeout,
                        )
                        self.logger.info("Init script completed successfully")
            else:
                # No boot prompt configured — just wait for final prompt
                self.logger.info("No boot prompt configured, waiting for QNX prompt...")
                with parent.serial.pexpect() as console:
                    console.expect(
                        self._final_prompt, timeout=self._prompt_timeout
                    )

            self._set_state(QnxState.QNX_READY)
            self.logger.info("QNX boot complete")
        except Exception:
            self._set_state(QnxState.BOARD_OFF)
            raise

    def off(self):
        """Power off the QNX board."""
        self.logger.info("Powering off board...")
        parent = self._parent_client
        parent.power_ctl.off()
        self._set_state(QnxState.BOARD_OFF)
        self.logger.info("Board powered off")

    def cycle(self, wait: int = 2):
        """Power cycle the QNX board."""
        self.off()
        time.sleep(wait)
        self.on()

    def status(self) -> str:
        """Return current power state."""
        with self._state_lock:
            state = self._state
        if state == QnxState.BOARD_OFF:
            return "off"
        elif state in (QnxState.QNX_BOOTING, QnxState.QNX_READY) or state in (
            QnxState.QVM_STARTING,
            QnxState.QVM_RUNNING,
            QnxState.QVM_STOPPING,
        ):
            return "on"
        return "unknown"

    @property
    def _parent_client(self):
        """Get the parent QnxClient composite."""
        # In jumpstarter, the parent composite client is accessible
        # because QnxPower is a child of the Qnx composite
        return self._composite_parent

    def cli(self):
        @driver_click_group(self)
        def base():
            """QNX board power control"""

        @base.command()
        def on():
            """Power on with full boot orchestration"""
            self.on()

        @base.command()
        def off():
            """Power off"""
            self.off()

        @base.command()
        @click.option(
            "--wait", "-w", default=2, help="Wait time in seconds between off and on"
        )
        def cycle(wait):
            """Power cycle"""
            self.cycle(wait)

        @base.command()
        def status():
            """Print the power state"""
            click.echo(self.status())

        return base


@dataclass(kw_only=True)
class QnxClient(CompositeClient):
    """Client for the QNX board composite driver."""

    @cached_property
    def _boot_prompt(self) -> str:
        return self.call("get_boot_prompt")

    @cached_property
    def _final_prompt(self) -> str:
        return self.call("get_final_prompt")


@dataclass(kw_only=True)
class QvmPowerClient(DriverClient):
    """Client for QVM power — launches/stops QVM via QNX serial console."""

    @cached_property
    def _qvm_path(self) -> str:
        return self.call("get_qvm_path")

    @cached_property
    def _working_directory(self) -> str:
        return self.call("get_working_directory")

    @cached_property
    def _qvm_config_path(self) -> str:
        return self.call("get_qvm_config_path")

    @cached_property
    def _uboot_bootcmd(self) -> str:
        return self.call("get_uboot_bootcmd")

    @cached_property
    def _qvm_start_timeout(self) -> int:
        return self.call("get_qvm_start_timeout")

    def on(self):
        """Power on QVM: ensure QNX is booted, upload config, launch QVM."""
        parent = self._composite_parent

        # Step 1: Ensure QNX is booted
        qnx_power_status = parent.qnx.power.status()
        if qnx_power_status == "off":
            self.logger.info("QNX is off, booting first...")
            parent.qnx.power.on()
        elif qnx_power_status != "on":
            self.logger.info("QNX state unclear, attempting boot...")
            parent.qnx.power.on()

        # Step 2: Upload rendered config via SSH
        rendered_config = self.call("get_rendered_config")
        if rendered_config:
            self.logger.info(f"Uploading QVM config to {self._qvm_config_path}")
            # Write config via SSH: echo into file
            parent.qnx.ssh.run(
                parent.qnx.ssh.__class__.SSHCommandRunOptions(capture_output=True),
                ["sh", "-c", f"cat > {self._qvm_config_path} << 'QVMEOF'\n{rendered_config}QVMEOF"],
            )

        # Update state on the QNX power client
        qnx_power = parent.qnx.power
        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_STARTING)

        # Step 3: Launch QVM via serial
        self.logger.info("Launching QVM...")

        with parent.qnx.serial.pexpect() as console:
            launch_cmd = (
                f"cd {self._working_directory} && "
                f"{self._qvm_path} @{self._qvm_config_path}"
            )
            console.sendline(launch_cmd)

            # Step 4-5: If uboot_bootcmd is set, interrupt U-Boot and send it
            if self._uboot_bootcmd:
                self.logger.info("Waiting for U-Boot prompt...")
                # Send spaces to interrupt U-Boot autoboot
                for _ in range(50):
                    console.send(" ")
                    try:
                        console.expect(r"=>|U-Boot>", timeout=0.2)
                        break
                    except pexpect.TIMEOUT:
                        continue
                else:
                    raise RuntimeError(
                        "Failed to interrupt U-Boot autoboot"
                    )

                self.logger.info(f"Sending bootcmd: {self._uboot_bootcmd}")
                console.sendline(self._uboot_bootcmd)

            # TODO: Wait for QVM boot confirmation
            # For now just wait a bit and declare success
            self.logger.info("QVM launched, waiting for boot...")
            time.sleep(2)

        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_RUNNING)

        self.logger.info("QVM is running")

    def off(self):
        """Stop QVM via slay command."""
        parent = self._composite_parent
        qnx_power = parent.qnx.power

        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_STOPPING)

        self.logger.info("Stopping QVM...")
        final_prompt = parent.qnx.call("get_final_prompt")

        with parent.qnx.serial.pexpect() as console:
            console.sendline("slay qvm")
            console.expect(final_prompt, timeout=10)

        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QNX_READY)

        self.logger.info("QVM stopped")

    def cycle(self, wait: int = 2):
        """Power cycle QVM."""
        self.off()
        time.sleep(wait)
        self.on()

    def status(self) -> str:
        """Return QVM power state."""
        parent = self._composite_parent
        qnx_power = parent.qnx.power
        if hasattr(qnx_power, "_state"):
            with qnx_power._state_lock:
                state = qnx_power._state
            if state == QnxState.QVM_RUNNING:
                return "on"
            elif state == QnxState.QVM_STARTING:
                return "starting"
            elif state == QnxState.QVM_STOPPING:
                return "stopping"
        return "off"

    def cli(self):
        @driver_click_group(self)
        def base():
            """QVM power control"""

        @base.command()
        def on():
            """Launch QVM"""
            self.on()

        @base.command()
        def off():
            """Stop QVM"""
            self.off()

        @base.command()
        @click.option(
            "--wait", "-w", default=2, help="Wait time in seconds between off and on"
        )
        def cycle(wait):
            """Restart QVM"""
            self.cycle(wait)

        @base.command()
        def status():
            """Print QVM power state"""
            click.echo(self.status())

        return base


@dataclass(kw_only=True)
class QvmFlasherClient(DriverClient):
    """Client for QVM storage flashing via fls with ssh:// target."""

    @cached_property
    def _emmc_device(self) -> str:
        return self.call("get_emmc_device")

    @cached_property
    def _ssh_password(self) -> str:
        return self.call("get_ssh_password")

    @cached_property
    def _flash_timeout(self) -> int:
        return self.call("get_flash_timeout")

    def _get_ssh_target(self) -> str:
        """Build the ssh:// target URL from the qnx driver's children."""
        parent = self._composite_parent

        # Get host and port from qnx.tcp
        tcp_address = parent.qnx.tcp.address()
        parsed = urlparse(tcp_address)
        host = parsed.hostname
        port = parsed.port

        # Get username from qnx.ssh
        username = parent.qnx.ssh.username

        # Build ssh:// URL
        port_suffix = f":{port}" if port and port != 22 else ""
        return f"ssh://{username}@{host}{port_suffix}{self._emmc_device}"

    def flash(self, source: str):
        """Flash an image to the QVM block device via fls.

        Args:
            source: Image source — OCI reference (oci://...) or HTTP(S) URL.
        """
        parent = self._composite_parent

        # Ensure QVM is stopped before flashing
        qvm_power = parent.power
        qvm_status = qvm_power.status()
        if qvm_status in ("on", "starting"):
            raise RuntimeError(
                "QVM must be powered off before flashing. "
                "Run 'j qvm power off' first."
            )

        # Ensure QNX is booted (we need SSH access)
        qnx_power_status = parent.qnx.power.status()
        if qnx_power_status == "off":
            self.logger.info("QNX is off, booting first...")
            parent.qnx.power.on()

        ssh_target = self._get_ssh_target()
        self.logger.info(f"Flashing {source} → {ssh_target}")

        fls_binary = get_fls_binary()
        fls_cmd = [fls_binary, "from-url", source, ssh_target]

        # Set up environment for fls SSH auth
        import os

        fls_env = os.environ.copy()
        if self._ssh_password:
            fls_env["SSHPASS"] = self._ssh_password

        self.logger.info(f"Running: {' '.join(fls_cmd)}")

        try:
            process = subprocess.Popen(
                fls_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=fls_env,
                text=True,
            )

            for line in iter(process.stdout.readline, ""):
                print(line, end="", flush=True)

            process.wait(timeout=self._flash_timeout)

            if process.returncode != 0:
                raise RuntimeError(
                    f"fls flash failed with exit code {process.returncode}"
                )

            self.logger.info("Flash completed successfully")
        except subprocess.TimeoutExpired as err:
            process.kill()
            raise RuntimeError(
                f"fls flash timed out after {self._flash_timeout}s"
            ) from err

    def cli(self):
        @driver_click_group(self)
        def base():
            """QVM storage management"""

        @base.command()
        @click.argument("source")
        def flash(source):
            """Flash image to QVM block device

            SOURCE is an OCI reference (oci://...) or HTTP(S) URL.
            """
            self.flash(source)

        return base


@dataclass(kw_only=True)
class QvmClient(CompositeClient):
    """Client for the QVM composite driver."""

    def get_qvm_config(self) -> str:
        """Read the current QVM config from the board via SSH."""
        qvm_config_path = self.call("get_qvm_config_path")
        result = self.qnx.ssh.run(
            self.qnx.ssh.__class__.SSHCommandRunOptions(capture_output=True),
            ["cat", qvm_config_path],
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Failed to read QVM config: {result.stderr}"
            )
        return result.stdout

    def set_qvm_config(self, content: str):
        """Write a new QVM config to the board via SSH."""
        qvm_config_path = self.call("get_qvm_config_path")
        self.qnx.ssh.run(
            self.qnx.ssh.__class__.SSHCommandRunOptions(capture_output=True),
            [
                "sh", "-c",
                f"cat > {qvm_config_path} << 'QVMEOF'\n{content}QVMEOF",
            ],
        )
        self.logger.info(f"QVM config written to {qvm_config_path}")

    def reset_qvm_config(self):
        """Restore QVM config from the default template."""
        rendered = self.call("get_rendered_config")
        if not rendered:
            raise RuntimeError(
                "No qvm_default_config is set in the driver configuration"
            )
        self.set_qvm_config(rendered)
        self.logger.info("QVM config reset to default")

    def cli(self):
        base = super().cli()

        @base.group()
        def config():
            """QVM configuration management"""

        @config.command()
        def show():
            """Show current QVM config from the board"""
            click.echo(self.get_qvm_config())

        @config.command("set")
        @click.argument("config_file", type=click.Path(exists=True))
        def set_config(config_file):
            """Upload a new QVM config to the board"""
            with open(config_file) as f:
                content = f.read()
            self.set_qvm_config(content)
            click.echo(f"Config uploaded from {config_file}")

        @config.command()
        def reset():
            """Reset QVM config to the default from exporter YAML"""
            self.reset_qvm_config()
            click.echo("Config reset to default")

        return base
