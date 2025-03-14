import sys
import time
from typing import Optional

import pexpect
from jumpstarter_driver_composite.client import CompositeClient

from .common import DhcpInfo


class UbootConsoleClient(CompositeClient):
    @property
    def prompt(self) -> str:
        return self.call("get_prompt")

    @prompt.setter
    def prompt(self, prompt: str):
        self.call("set_prompt", prompt)

    def reboot_to_console(self):
        """Trigger U-Boot console
        Power cycle the target and wait for the U-Boot prompt
        """
        self.power.cycle()
        self.logger.info("Waiting for U-Boot prompt...")
        data = b""
        for _ in range(100):
            self.console.send("\x1b")
            try:
                recv = self.console.read_nonblocking(size=4096, timeout=0.1)
                if recv:
                    data += recv
            except pexpect.TIMEOUT:
                pass
            # print(data)
            if self.prompt.encode() in data:
                return self.console.send("\x1b")
            time.sleep(0.1)
        raise RuntimeError("Failed to get U-Boot prompt")

    def run_command(self, cmd: str, timeout: int = 60):
        self.logger.info(f"Running command: {cmd}")
        if not cmd.endswith("\n"):
            cmd += "\n"
        self.console.send(cmd.encode("utf-8"))
        return self._read_until(self.prompt, timeout)

    def setup_dhcp(self, timeout: int = 60) -> DhcpInfo:
        self.logger.info("Running DHCP to obtain network configuration...")
        buffer = self.run_command("dhcp", timeout)

        # Extract IP and
        ip_address = None
        gateway = None

        for line in buffer.splitlines():
            if "DHCP client bound to address" in line:
                bind_index = line.find("DHCP client bound to address") + len("DHCP client bound to address")
                ip_end = line.find("(", bind_index)
                if ip_end != -1:
                    ip_address = line[bind_index:ip_end].strip()

            if "sending through gateway" in line:
                gw_index = line.find("sending through gateway") + len("sending through gateway")
                gateway = line[gw_index:].strip()

        if not ip_address or not gateway:
            raise ValueError("Could not extract complete network information")

        # Get netmask from environment
        netmask = self.get_env("netmask") or "255.255.255.0"

        return DhcpInfo(ip_address=ip_address, gateway=gateway, netmask=netmask)

    def wait_for_pattern(self, pattern: str, timeout: int = 300, print_output: bool = False):
        """Wait for specific pattern in output"""
        return self._read_until(pattern, timeout, print_output)

    def get_env(self, var_name: str, timeout: int = 5) -> Optional[str]:
        """Get U-Boot environment variable value"""
        self.logger.debug(f"\nGetting U-Boot env var: {var_name}")
        try:
            buffer = self.run_command(f"printenv {var_name}", timeout)
            for line in buffer.splitlines():
                if f"{var_name}=" in line:
                    return line.split("=", 1)[1].strip()
        except TimeoutError as err:
            raise TimeoutError(f"Timed out waiting for {var_name}") from err

        return None

    def set_env(self, key: str, value: str):
        cmd = f"setenv {key} '{value}'"
        self.logger.debug(f"Sending command to U-Boot: {cmd}")
        self.run_command(cmd, timeout=5)

    def set_env_dict(self, env):
        for key, value in env.items():
            self.set_env(key, value)

    # TODO: rewrite this, there is a way to do it just with pexpect
    #  https://github.com/jumpstarter-dev/jumpstarter-devspace/blob/orin-nx-testing/tests/test_on_orin_nx.py#L156
    def _read_until(
        self, target: str, timeout: int = 60, print_output: bool = False, error_patterns: list[str] = None
    ) -> str:
        saved_logfile = self.console.logfile_read
        self.logger.debug(f"_read_until {target}")
        try:
            if print_output:
                self.console.logfile_read = sys.stdout.buffer

            self.console.expect(target, timeout=timeout)
            buffer = self.console.before.decode().strip()
            if error_patterns and any(pattern in buffer.lower() for pattern in error_patterns):
                raise RuntimeError(f"Error detected in output: {buffer}")
            return buffer
        except pexpect.TIMEOUT as err:
            raise TimeoutError(f"Timed out waiting for '{target}'") from err
        except RuntimeError:
            raise
        finally:
            self.console.logfile_read = saved_logfile
