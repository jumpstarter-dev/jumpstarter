import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.decorators import driver_click_command


def redact_password_in_args(args):
    """Redact password arguments in a list for safe logging"""
    redacted = list(args)
    try:
        # Find -p flag and redact the next argument (password)
        p_index = redacted.index("-p")
        if p_index + 1 < len(redacted):
            redacted[p_index + 1] = "******"
    except ValueError:
        # -p flag not found, nothing to redact
        pass
    return redacted


@dataclass(kw_only=True)
class TMTClient(CompositeClient):
    """
    Client interface for LocalTMT driver

    This client provides methods to interact with LocalTMT devices via SSH
    """

    def cli(self):

        @driver_click_command(
            self,
            context_settings={"ignore_unknown_options": True},
            help="Run TMT command with arguments",
        )
        @click.option("--forward-ssh", is_flag=True)
        @click.option("--tmt-username", default=None)
        @click.option("--tmt-password", default=None)
        @click.option("--tmt-cmd", default="tmt")
        @click.option("--tmt-on-exporter", is_flag=True)
        @click.argument("args", nargs=-1)
        def tmt(forward_ssh, tmt_username, tmt_password, tmt_cmd, tmt_on_exporter, args):
            if tmt_on_exporter:
                click.echo("TMT will be run on the exporter")
                raise click.Abort("Still not implemented")
            else:
                result = self.run_tmt_local(forward_ssh, tmt_cmd, tmt_username, tmt_password, args)
                self.logger.debug(f"TMT result: {result}")
                if result != 0:
                    click.get_current_context().exit(result)
                return result

        return tmt

    def run_tmt_local(self, forward_ssh, tmt_cmd, username, password, args):
        # if we are asked to forward the ssh connection, or we have to fallback, we do that
        def_user, def_pass = self.call("get_default_user_pass")
        username = username or def_user
        password = password or def_pass
        hard_reboot_cmd = self.call("get_reboot_cmd")
        if forward_ssh:
            self.logger.debug("Using SSH port forwarding for TMT connection")
            with TcpPortforwardAdapter(
                client=self.ssh,
            ) as addr:
                host = addr[0]
                port = addr[1]
                self.logger.debug(f"SSH port forward established - host: {host}, port: {port}")
                return self._run_tmt_local(host, port, tmt_cmd, username, password, hard_reboot_cmd, args)
        else:
            # if we are not asked to forward the ssh connection, we try to get the address from the ssh driver
            try:
                address = self.ssh.address() # (format: "tcp://host:port")
                parsed = urlparse(address)
                host = parsed.hostname
                port = parsed.port
                if not host or not port:
                    raise ValueError(f"Invalid address format: {address}")
                self.logger.debug(f"Using direct SSH connection for tmt - host: {host}, port: {port}")
                return self._run_tmt_local(host, port, tmt_cmd, username, password, hard_reboot_cmd, args)
            except (DriverMethodNotImplemented, ValueError) as e:
                self.logger.warning(f"Direct address connection failed ({e}), falling back to SSH port forwarding")
                return self.run_tmt_local(True, tmt_cmd, username, password, args)

    def _run_tmt_local(self, host, port, tmt_cmd, username, password, hard_reboot_cmd, args):
        """Run TMT command with the given host, port, and arguments"""
        # This is a placeholder implementation - replace with actual TMT command execution
        args = replace_provision_args(self.logger, args, host, port, username, password, hard_reboot_cmd)
        # Redact password for safe logging
        safe_args = redact_password_in_args(args)
        self.logger.debug(f"Running TMT command: {[tmt_cmd] + safe_args}")
        # execute the command on the local machine
        try:
            result = subprocess.run([tmt_cmd] + args)
            return result.returncode
        except FileNotFoundError:
            self.logger.error(
                f"TMT command '{tmt_cmd}' not found. Please ensure TMT is installed and available in PATH."
            )
            return 127  # Standard exit code for "command not found"

# the tmt commands are executed locally, but we need to identify
# the connection part of the commandline and replace it or insert our own
# this is a possible set of args that we may receive:
# --root . -c tracing=off -c arch=aarch64 -c distro=rhel-9 -c hw_target=rcar_s4 run
# --workdir-root /tmp/ -a -vv provision -h connect -g $IP -P $PORT -u root -p password --help plan -vv
# --name ^/podman/plans/fusa/tests$
#
# in this case we need to identify the provision section and replace it with our own,
# and reuse the -u root -p password if provided
# provision can have any number of -flag arguments, so we need to identify them and replace them with our own
def replace_provision_args(logger, args, host, port, username, password, hard_reboot_cmd):
    """Replace or add provision arguments for TMT command"""
    # this list is used to identify the end of the provision section
    TMT_RUN_CMDS = [
        "discover", "provision", "prepare", "execute", "report", "finish", "cleanup",
        "login", "reboot", "plan", "plans", "test", "run"
    ]
    # find the provision section
    provision_args = ["provision","-h", "connect", "-g", host, "-P", str(port)]
    if username:
        provision_args.append("-u")
        provision_args.append(username)
    if password:
        provision_args.append("-p")
        provision_args.append(password)
    if hard_reboot_cmd:
        provision_args.append("--feeling-safe")
        provision_args.append("--hard-reboot")
        provision_args.append(hard_reboot_cmd)
    try:
        provision_index = args.index("provision")
    except ValueError:
        # "provision" not found in args
        if "run" in args:
            logger.debug("Run section found, adding provision arguments")
            return list(args) + provision_args
        else:
            logger.debug("Provision or run section not found, ignoring")
            return list(args)

    next_cmd_index = provision_index + 1
    while next_cmd_index < len(args) and args[next_cmd_index] not in TMT_RUN_CMDS:
        next_cmd_index += 1
    # Redact passwords for safe logging
    safe_provision_section = redact_password_in_args(args[provision_index:next_cmd_index])
    safe_provision_args = redact_password_in_args(provision_args)
    logger.debug(f"Provision to be replaced: {safe_provision_section}")
    logger.debug(f"Will be replaced with: {safe_provision_args}")
    # get the provision section
    before_provision = args[:provision_index]
    after_provision = args[next_cmd_index:]

    args = list(before_provision) + provision_args + list(after_provision)
    return args

