import shlex
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented


@dataclass(kw_only=True)
class SSHWrapperClient(CompositeClient):
    """
    Client interface for SSHWrapper driver

    This client provides methods to interact with SSH connections via CLI
    """

    def cli(self, click_group):
        @click_group.command(context_settings={"ignore_unknown_options": True})
        @click.option("--direct", is_flag=True, help="Use direct TCP address (default)")
        @click.argument("args", nargs=-1)
        def ssh(direct, args):
            """Run SSH command with arguments"""
            result = self.run(direct, args)
            self.logger.debug(f"SSH result: {result}")
            if result != 0:
                click.get_current_context().exit(result)
            return result

        return ssh

    # wrap the underlying tcp stream connections, so we can still use tcp forwarding or
    # the fabric driver adapter on top of client.ssh
    def stream(self, method="connect"):
        return self.tcp.stream(method)

    async def stream_async(self, method):
        return await self.tcp.stream_async(method)

    def run(self, direct, args):
        """Run SSH command with the given parameters and arguments"""
        # Get SSH command and default username from driver
        ssh_command = self.call("get_ssh_command")
        default_username = self.call("get_default_username")

        if direct:
            # Use direct TCP address
            try:
                address = self.tcp.address()  # (format: "tcp://host:port")
                parsed = urlparse(address)
                host = parsed.hostname
                port = parsed.port
                if not host or not port:
                    raise ValueError(f"Invalid address format: {address}")
                self.logger.debug(f"Using direct TCP connection for SSH - host: {host}, port: {port}")
                return self._run_ssh_local(host, port, ssh_command, default_username, args)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.error(f"Direct address connection failed ({e}), falling back to SSH port forwarding")
                return self.run(False, args)
        else:
            # Use SSH port forwarding (default behavior)
            self.logger.debug("Using SSH port forwarding for SSH connection")
            with TcpPortforwardAdapter(
                client=self.tcp,
            ) as addr:
                host = addr[0]
                port = addr[1]
                self.logger.debug(f"SSH port forward established - host: {host}, port: {port}")
                return self._run_ssh_local(host, port, ssh_command, default_username, args)

    def _run_ssh_local(self, host, port, ssh_command, default_username, args):
        """Run SSH command with the given host, port, and arguments"""
        # Build SSH command arguments
        ssh_args = self._build_ssh_command_args(ssh_command, port, default_username, args)

        # Separate SSH options from command arguments
        ssh_options, command_args = self._separate_ssh_options_and_command_args(args)

        # Build final SSH command
        ssh_args = self._build_final_ssh_command(ssh_args, ssh_options, host, command_args)

        # Execute the command
        return self._execute_ssh_command(ssh_args)

    def _build_ssh_command_args(self, ssh_command, port, default_username, args):
        """Build initial SSH command arguments"""
        # Split the SSH command into individual arguments
        ssh_args = shlex.split(ssh_command)

        # Add port if specified
        if port and port != 22:
            ssh_args.extend(["-p", str(port)])

        # Check if user already provided a username with -l flag in SSH options only
        # We need to separate SSH options from command args first to avoid false positives
        ssh_options, _ = self._separate_ssh_options_and_command_args(args)
        has_user_flag = any(
            ssh_options[i] == "-l" and i + 1 < len(ssh_options)
            for i in range(len(ssh_options))
        )

        # Add default username if no -l flag provided and we have a default
        if not has_user_flag and default_username:
            ssh_args.extend(["-l", default_username])

        return ssh_args


    def _separate_ssh_options_and_command_args(self, args):
        """Separate SSH options from command arguments"""
        # SSH flags that do not expect a parameter (simple flags)
        ssh_flags_no_param = {
            '-4', '-6', '-A', '-a', '-C', '-f', '-G', '-g', '-K', '-k', '-M', '-N',
            '-n', '-q', '-s', '-T', '-t', '-V', '-v', '-X', '-x', '-Y', '-y'
        }

        # SSH flags that do expect a parameter
        ssh_flags_with_param = {
            '-B', '-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l',
            '-m', '-O', '-o', '-P', '-p', '-Q', '-R', '-S', '-W', '-w'
        }

        ssh_options = []
        command_args = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('-'):
                # Check if it's a known SSH option
                if arg in ssh_flags_no_param:
                    # This is a simple SSH flag without parameter
                    ssh_options.append(arg)
                elif arg in ssh_flags_with_param:
                    # This is an SSH flag that expects a parameter
                    ssh_options.append(arg)
                    # If this option takes a value, add the next argument too
                    if i + 1 < len(args) and not args[i + 1].startswith('-'):
                        ssh_options.append(args[i + 1])
                        i += 1
                else:
                    # This is a command argument - everything from here on is part of the command
                    command_args = args[i:]
                    break
            else:
                # This is a command argument - everything from here on is part of the command
                command_args = args[i:]
                break
            i += 1

        # Debug output
        self.logger.debug(f"SSH options: {ssh_options}")
        self.logger.debug(f"Command args: {command_args}")
        return ssh_options, command_args


    def _build_final_ssh_command(self, ssh_args, ssh_options, host, command_args):
        """Build the final SSH command with all components"""
        # Add SSH options
        ssh_args.extend(ssh_options)

        # Add hostname before command arguments
        if host:
            ssh_args.append(host)

        # Add command arguments
        ssh_args.extend(command_args)

        self.logger.debug(f"Running SSH command: {ssh_args}")
        return ssh_args

    def _execute_ssh_command(self, ssh_args):
        """Execute the SSH command and return the result"""
        try:
            result = subprocess.run(ssh_args)
            return result.returncode
        except FileNotFoundError:
            self.logger.error(
                f"SSH command '{ssh_args[0]}' not found. Please ensure SSH is installed and available in PATH."
            )
            return 127  # Standard exit code for "command not found"
