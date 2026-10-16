import os
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
from jumpstarter_driver_ssh.client import SSHCommandRunOptions

from jumpstarter.client.decorators import driver_click_group
from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.common.fls import get_fls_binary


class QnxError(JumpstarterException):
    """Base exception for QNX/QVM driver errors (surfaced cleanly by ``j``)."""


class QnxTimeoutError(QnxError):
    """Raised when a QNX/QVM serial expect or operation times out."""


class QnxState(Enum):
    """State machine for QNX/QVM serial port ownership."""

    BOARD_OFF = auto()
    QNX_BOOTING = auto()
    QNX_READY = auto()
    QVM_STARTING = auto()
    QVM_RUNNING = auto()
    QVM_STOPPING = auto()


@dataclass(kw_only=True)
class QnxPowerClient(CompositeClient):
    """Client for the orchestrated QNX power driver.

    Orchestrates: power_ctl.cycle() → IPL boot menu → init_script.
    Children: power_ctl, serial (shared with parent Qnx).
    """

    _state_lock: threading.Lock = field(default_factory=threading.Lock)
    _state: QnxState = QnxState.BOARD_OFF
    _state_event: threading.Event = field(default_factory=threading.Event)

    @cached_property
    def _config(self) -> dict:
        return self.call("get_config")

    @property
    def _boot_prompt(self) -> str:
        return self._config["boot_prompt"]

    @property
    def _boot_key(self) -> str:
        return self._config["boot_key"]

    @property
    def _final_prompt(self) -> str:
        return self._config["final_prompt"]

    @property
    def _init_script(self) -> str:
        return self._config["init_script"]

    @property
    def _boot_timeout(self) -> int:
        return self._config["boot_timeout"]

    @property
    def _prompt_timeout(self) -> int:
        return self._config["prompt_timeout"]

    def _set_state(self, state: QnxState):
        with self._state_lock:
            self._state = state
            if state == QnxState.QVM_RUNNING:
                self._state_event.set()
            else:
                self._state_event.clear()

    @staticmethod
    def _console_text(console) -> str:
        before = getattr(console, "before", None) or b""
        if isinstance(before, bytes):
            return before.decode(errors="replace")
        return str(before)

    def _expect(self, console, pattern: str, timeout: int, what: str):
        """pexpect.expect wrapping timeouts as QnxTimeoutError for ``j``."""
        try:
            console.expect(pattern, timeout=timeout)
        except pexpect.TIMEOUT as err:
            output = self._console_text(console).strip()
            detail = f"\n--- serial output ---\n{output}\n---" if output else ""
            raise QnxTimeoutError(
                f"Timed out waiting for {what} {pattern!r} after {timeout}s{detail}"
            ) from err

    def _run_init_script(self, console):
        self.logger.info("Running init script...")
        # Run as one compound command so $? is the overall exit code
        script = self._init_script.rstrip("\n")
        console.sendline("{")
        for line in script.splitlines():
            console.sendline(line)
        console.sendline("}; echo INIT_EXIT_CODE=$?")
        try:
            console.expect(r"INIT_EXIT_CODE=(\d+)", timeout=self._prompt_timeout)
        except pexpect.TIMEOUT as err:
            output = self._console_text(console).strip()
            detail = f"\n--- serial output ---\n{output}\n---" if output else ""
            raise QnxTimeoutError(
                f"Timed out waiting for init script exit code after {self._prompt_timeout}s{detail}"
            ) from err

        exit_code = int(console.match.group(1))
        output = self._console_text(console).strip()
        if exit_code != 0:
            detail = f"\n--- serial output ---\n{output}\n---" if output else ""
            raise QnxError(f"Init script failed with exit code {exit_code}{detail}")

        self._expect(
            console, self._final_prompt, self._prompt_timeout, "QNX prompt after init script"
        )
        self.logger.info("Init script completed successfully")

    def _boot_via_ipl(self, console):
        self.logger.info(f"Waiting for IPL boot prompt: {self._boot_prompt!r}")
        # Send space to trigger re-prompt if serial opened late
        console.send(" ")
        self._expect(console, self._boot_prompt, self._boot_timeout, "IPL boot prompt")
        self.logger.info(f"Sending boot key: {self._boot_key!r}")
        console.send(self._boot_key)
        self.logger.info(f"Waiting for QNX prompt: {self._final_prompt!r}")
        self._expect(console, self._final_prompt, self._prompt_timeout, "QNX prompt")
        if self._init_script:
            self._run_init_script(console)

    def on(self):
        """Power on QNX board with full boot orchestration."""
        self._set_state(QnxState.QNX_BOOTING)
        try:
            self.logger.info("Power cycling board...")
            self.power_ctl.cycle()

            with self.serial.pexpect() as console:
                if self._boot_prompt and self._boot_key:
                    self._boot_via_ipl(console)
                else:
                    self.logger.info("No boot prompt configured, waiting for QNX prompt...")
                    self._expect(
                        console, self._final_prompt, self._prompt_timeout, "QNX prompt"
                    )

            self._set_state(QnxState.QNX_READY)
            self.logger.info("QNX boot complete")
        except Exception:
            self._set_state(QnxState.BOARD_OFF)
            raise

    def off(self):
        """Power off the QNX board."""
        self.logger.info("Powering off board...")
        self.power_ctl.off()
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
        if state in (
            QnxState.QNX_BOOTING,
            QnxState.QNX_READY,
            QnxState.QVM_STARTING,
            QnxState.QVM_RUNNING,
            QnxState.QVM_STOPPING,
        ):
            return "on"
        return "unknown"

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
    def _final_prompt(self) -> str:
        return self.call("get_final_prompt")


@dataclass(kw_only=True)
class QvmPowerClient(CompositeClient):
    """Client for QVM power — launches/stops QVM via QNX serial console.

    Children: qnx (shared with parent Qvm).
    """

    @cached_property
    def _config(self) -> dict:
        return self.call("get_config")

    @property
    def _qvm_path(self) -> str:
        return self._config["qvm_path"]

    @property
    def _working_directory(self) -> str:
        return self._config["working_directory"]

    @property
    def _qvm_config_path(self) -> str:
        return self._config["qvm_config_path"]

    @property
    def _uboot_bootcmd(self) -> str:
        return self._config["uboot_bootcmd"]

    @property
    def _qvm_start_timeout(self) -> int:
        return self._config["qvm_start_timeout"]

    def _write_config_via_ssh(self, content: str):
        """Write QVM config to the board via SSH."""
        opts = SSHCommandRunOptions(capture_output=True)
        result = self.qnx.ssh.run(
            opts,
            [
                "sh",
                "-c",
                f"cat > {self._qvm_config_path} << 'QVMEOF'\n{content}QVMEOF",
            ],
        )
        if result.return_code != 0:
            raise QnxError(f"Failed to write QVM config: {result.stderr}")

    def on(self):
        """Power on QVM: ensure QNX is booted, upload config, launch QVM."""
        qnx_power = self.qnx.power
        qnx_power_status = qnx_power.status()
        if qnx_power_status != "on":
            self.logger.info("QNX is not ready, booting first...")
            qnx_power.on()

        rendered_config = self._config["rendered_config"]
        if rendered_config:
            self.logger.info(f"Uploading QVM config to {self._qvm_config_path}")
            self._write_config_via_ssh(rendered_config)

        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_STARTING)

        self.logger.info("Launching QVM...")
        with self.qnx.serial.pexpect() as console:
            launch_cmd = (
                f"cd {self._working_directory} && "
                f"{self._qvm_path} @{self._qvm_config_path}"
            )
            console.sendline(launch_cmd)

            if self._uboot_bootcmd:
                self.logger.info("Waiting for U-Boot prompt...")
                for _ in range(50):
                    console.send(" ")
                    try:
                        console.expect(r"=>|U-Boot>", timeout=0.2)
                        break
                    except pexpect.TIMEOUT:
                        continue
                else:
                    raise QnxTimeoutError("Failed to interrupt U-Boot autoboot")

                self.logger.info(f"Sending bootcmd: {self._uboot_bootcmd}")
                console.sendline(self._uboot_bootcmd)

            # TODO: Wait for QVM boot confirmation
            self.logger.info("QVM launched, waiting for boot...")
            time.sleep(2)

        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_RUNNING)

        self.logger.info("QVM is running")

    def off(self):
        """Stop QVM via slay command."""
        qnx_power = self.qnx.power
        if hasattr(qnx_power, "_set_state"):
            qnx_power._set_state(QnxState.QVM_STOPPING)

        self.logger.info("Stopping QVM...")
        final_prompt = self.qnx.call("get_final_prompt")

        try:
            with self.qnx.serial.pexpect() as console:
                console.sendline("slay qvm")
                console.expect(final_prompt, timeout=10)
        except pexpect.TIMEOUT as err:
            raise QnxTimeoutError(
                f"Timed out waiting for QNX prompt after slay qvm: {final_prompt!r}"
            ) from err

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
        qnx_power = self.qnx.power
        if hasattr(qnx_power, "_state"):
            with qnx_power._state_lock:
                state = qnx_power._state
            if state == QnxState.QVM_RUNNING:
                return "on"
            if state == QnxState.QVM_STARTING:
                return "starting"
            if state == QnxState.QVM_STOPPING:
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
class QvmFlasherClient(CompositeClient):
    """Client for QVM storage flashing via fls with ssh:// target.

    Children: qnx (shared with parent Qvm).
    """

    @cached_property
    def _config(self) -> dict:
        return self.call("get_config")

    @property
    def _emmc_device(self) -> str:
        return self._config["emmc_device"]

    @property
    def _ssh_password(self) -> str:
        return self._config["ssh_password"]

    @property
    def _flash_timeout(self) -> int:
        return self._config["flash_timeout"]

    def _get_ssh_target(self) -> str:
        """Build the ssh:// target URL from the qnx driver's children."""
        tcp_address = self.qnx.tcp.address()
        parsed = urlparse(tcp_address)
        host = parsed.hostname
        port = parsed.port
        username = self.qnx.ssh.username

        port_suffix = f":{port}" if port and port != 22 else ""
        return f"ssh://{username}@{host}{port_suffix}{self._emmc_device}"

    def flash(self, source: str):
        """Flash an image to the QVM block device via fls.

        Args:
            source: Image source — OCI reference (oci://...) or HTTP(S) URL.
        """
        # Ensure QNX is booted (we need SSH access)
        qnx_power_status = self.qnx.power.status()
        if qnx_power_status != "on":
            self.logger.info("QNX is off, booting first...")
            self.qnx.power.on()

        # Prefer checking QVM status if available via sibling path on parent —
        # QvmFlasher only has qnx as child; caller should power off QVM first.
        # Soft check: if qnx reports QVM_RUNNING, refuse.
        qnx_power = self.qnx.power
        if hasattr(qnx_power, "_state"):
            with qnx_power._state_lock:
                state = qnx_power._state
            if state in (QnxState.QVM_RUNNING, QnxState.QVM_STARTING):
                raise QnxError(
                    "QVM must be powered off before flashing. "
                    "Run 'j qvm power off' first."
                )

        ssh_target = self._get_ssh_target()
        self.logger.info(f"Flashing {source} → {ssh_target}")

        fls_binary = get_fls_binary()
        fls_cmd = [fls_binary, "from-url", source, ssh_target]

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

            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                print(line, end="", flush=True)

            process.wait(timeout=self._flash_timeout)

            if process.returncode != 0:
                raise QnxError(
                    f"fls flash failed with exit code {process.returncode}"
                )

            self.logger.info("Flash completed successfully")
        except subprocess.TimeoutExpired as err:
            process.kill()
            raise QnxTimeoutError(
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
            SSHCommandRunOptions(capture_output=True),
            ["cat", qvm_config_path],
        )
        if result.return_code != 0:
            raise QnxError(f"Failed to read QVM config: {result.stderr}")
        return result.stdout

    def set_qvm_config(self, content: str):
        """Write a new QVM config to the board via SSH."""
        qvm_config_path = self.call("get_qvm_config_path")
        result = self.qnx.ssh.run(
            SSHCommandRunOptions(capture_output=True),
            [
                "sh",
                "-c",
                f"cat > {qvm_config_path} << 'QVMEOF'\n{content}QVMEOF",
            ],
        )
        if result.return_code != 0:
            raise QnxError(f"Failed to write QVM config: {result.stderr}")
        self.logger.info(f"QVM config written to {qvm_config_path}")

    def reset_qvm_config(self):
        """Restore QVM config from the default template."""
        rendered = self.call("get_rendered_config")
        if not rendered:
            raise QnxError(
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
