from contextlib import contextmanager
from functools import cached_property

import pexpect
from jumpstarter_driver_composite.client import CompositeClient

from .common import ESC, DhcpInfo


class UbootConsoleClient(CompositeClient):
    @cached_property
    def prompt(self) -> str:
        return self.call("get_prompt")

    @contextmanager
    def reboot_to_console(self) -> None:
        """
        Reboot to U-Boot console

        Power cycle the target and wait for the U-Boot prompt
        """

        self.logger.info("Power cycling target...")
        self.power.cycle()

        self.logger.info("Waiting for U-Boot prompt...")

        with self.serial.pexpect() as p:
            for _ in range(100):  # TODO: configurable retries
                try:
                    p.send(ESC)
                    p.expect_exact(self.prompt, timeout=0.1)
                except pexpect.TIMEOUT:
                    continue

                break
            else:
                raise RuntimeError("Failed to get U-Boot prompt")

            self.p = p
            try:
                yield
            finally:
                delattr(self, "p")

    def run_command(self, cmd: str, timeout: int = 60, *, _internal_log=True) -> bytes:
        if _internal_log:
            self.logger.info(f"Running command: {cmd}")
        if not hasattr(self, "p"):
            raise RuntimeError("Not in a reboot_to_console context")
        self.p.sendline("")
        self.p.expect_exact(self.prompt, timeout=timeout)
        self.p.sendline(cmd)
        self.p.expect_exact(self.prompt, timeout=timeout)
        return self.p.before

    def run_command_checked(self, cmd: str, timeout: int = 60, check=True) -> list[str]:
        self.logger.info(f"Running command checked: {cmd}")
        output = self.run_command("{}; echo $?".format(cmd), _internal_log=False)
        parsed = output.strip().decode().splitlines()

        if len(parsed) < 2:
            raise RuntimeError("Insufficient lines returned from command execution, raw output: {}".format(output))

        try:
            retval = int(parsed[-1])
        except ValueError:
            raise ValueError("Failed to parse command return value: {}", parsed[-1]) from None

        if check and retval != 0:
            raise RuntimeError("Command failed with return value: {}, output: {}".format(retval, output))

        return parsed[1:-1]

    def setup_dhcp(self, timeout: int = 60) -> DhcpInfo:
        self.logger.info("Running DHCP to obtain network configuration...")

        autoload = self.get_env("autoload", timeout=timeout)
        self.set_env("autoload", "no")
        self.run_command_checked("dhcp", timeout=timeout)
        self.set_env("autoload", autoload)

        ipaddr = self.get_env("ipaddr")
        gatewayip = self.get_env("gatewayip")
        netmask = self.get_env("netmask") or "255.255.255.0"

        if not ipaddr or not gatewayip:
            raise ValueError("Could not extract complete network information")

        return DhcpInfo(ip_address=ipaddr, gateway=gatewayip, netmask=netmask)

    def get_env(self, key: str, timeout: int = 5) -> str | None:
        """
        Get U-Boot environment variable value
        """

        self.logger.debug(f"Getting U-Boot env var: {key}")
        try:
            output = self.run_command_checked("printenv {}".format(key), timeout, check=False)
            if len(output) != 1:
                raise RuntimeError(
                    "Invalid number of lines returned from printenv command, output: {}".format(output),
                )

            if output[0].startswith("## Error") and output[0].endswith("not defined"):
                return None

            parsed = output[0].split("=", 1)
            if len(parsed) != 2:
                raise RuntimeError(
                    "Failed to parse output of printenv command, output: {}".format(output[0]),
                )

            return parsed[1]
        except TimeoutError as err:
            raise TimeoutError(f"Timed out getting var {key}") from err

    def set_env(self, key: str, value: str | None, timeout: int = 5) -> None:
        if value is not None:
            cmd = "setenv {} '{}'".format(key, value)
        else:
            cmd = "setenv {}".format(key)

        try:
            self.run_command_checked(cmd, timeout=5)
        except TimeoutError as err:
            raise TimeoutError(f"Timed out setting var {key}") from err

    def set_env_dict(self, env: dict[str, str | None]) -> None:
        for key, value in env.items():
            self.set_env(key, value)
