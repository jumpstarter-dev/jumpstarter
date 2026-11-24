"""The `j-forward` command: the forwards of the lease of a `jmp shell` (JEP-0016, DD-12).

It is a command of its own, and not `j forward`: the names below `j` are the
drivers of an exporter or the roles of a lease, and a reserved word there
could be the name of one of them.
"""

import os

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions
from jumpstarter_cli_common.opt import OutputMode, OutputType
from jumpstarter_cli_common.print import model_print
from pydantic import BaseModel, model_serializer

from .describe import forward_directions
from jumpstarter.client.grpc import ForwardEndpoint, Lease, LeaseForward
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import JMP_LEASE
from jumpstarter.utils.env import JMP_LEASE_FORWARDS, lease_forwards_from_env

opt_output = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.JSON, OutputMode.YAML, OutputMode.NAME]),
    default=None,
    help="Output mode. Defaults to a table.",
)


class ForwardStatusEndpoint(BaseModel):
    member: str
    port: str
    # provides, requires or connects, when the exporter of the member reports the port.
    direction: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unknown_direction(self, handler):
        data = handler(self)
        if self.direction is None:
            data.pop("direction", None)
        return data


class ForwardStatus(BaseModel):
    name: str
    endpoints: list[ForwardStatusEndpoint]
    state: str
    message: str | None = None

    def describe(self) -> str:
        """`a.x → b.y` from the `provides` port, `a.x ↔ b.y` for a path, or the endpoints in the order of the lease."""
        forward = LeaseForward(
            name=self.name,
            endpoints=[ForwardEndpoint(member_name=e.member, port_name=e.port) for e in self.endpoints],
        )
        return forward.describe({(e.member, e.port): e.direction for e in self.endpoints if e.direction})


class ForwardStatusList(BaseModel):
    lease: str
    forwards: list[ForwardStatus]

    def _show_message(self) -> bool:
        return any(forward.message for forward in self.forwards)

    def rich_add_columns(self, table, **kwargs):
        table.add_column("NAME", no_wrap=True)
        table.add_column("ENDPOINTS")
        table.add_column("STATE")
        # The cause of a Failed or Disabled forward.
        if self._show_message():
            table.add_column("MESSAGE")

    def rich_add_rows(self, table, **kwargs):
        show_message = self._show_message()
        for forward in self.forwards:
            table.add_row(
                forward.name,
                forward.describe(),
                forward.state,
                *([forward.message or ""] if show_message else []),
            )

    def rich_add_names(self, names):
        for forward in self.forwards:
            names.append(forward.name)


def _from_lease(config, lease: Lease, snapshot: list[dict]) -> ForwardStatusList:
    # The shell read the directions of the ports when it started. Ask the
    # exporters only for what it could not read.
    directions = {
        (endpoint.get("member"), endpoint.get("port")): endpoint["direction"]
        for entry in snapshot
        for endpoint in entry.get("endpoints") or []
        if endpoint.get("direction")
    }
    endpoints = {(e.member_name, e.port_name) for forward in lease.forwards for e in forward.endpoints}
    if not endpoints <= set(directions):
        directions = {**directions, **forward_directions(config, lease)}
    forwards = []
    for forward in lease.forwards:
        status = lease.forward_status(forward.name)
        forwards.append(
            ForwardStatus(
                name=forward.name,
                endpoints=[
                    ForwardStatusEndpoint(
                        member=e.member_name,
                        port=e.port_name,
                        direction=directions.get((e.member_name, e.port_name)),
                    )
                    for e in forward.endpoints
                ],
                state=lease.forward_state(forward.name),
                message=status.message if status is not None else None,
            )
        )
    return ForwardStatusList(lease=lease.name, forwards=forwards)


def _from_snapshot(name: str, snapshot: list[dict]) -> ForwardStatusList:
    return ForwardStatusList(
        lease=name,
        forwards=[
            ForwardStatus(
                name=entry["name"],
                endpoints=[
                    ForwardStatusEndpoint(
                        member=e.get("member", ""), port=e.get("port", ""), direction=e.get("direction")
                    )
                    for e in entry.get("endpoints") or []
                ],
                state=entry.get("state") or "Pending",
                message=entry.get("message"),
            )
            for entry in snapshot
        ],
    )


def forward_status_list(config, name: str, *, in_shell: bool) -> ForwardStatusList:
    """The forwards of a lease, with their state from the lease status.

    in_shell says that the lease is the one of this shell. If the lease
    cannot be read then, the result has the states that `jmp shell` recorded
    when it started, with a warning.
    """
    snapshot = lease_forwards_from_env() if in_shell else []
    has_snapshot = in_shell and JMP_LEASE_FORWARDS in os.environ
    if not isinstance(config, ClientConfigV1Alpha1):
        if not has_snapshot:
            raise click.ClickException(
                f"cannot read lease {name}: no client config is set. Use --client or --client-config"
            )
        reason = "no client config is set"
    else:
        try:
            return _from_lease(config, config.get_lease(name=name), snapshot)
        except Exception as e:
            if not has_snapshot:
                raise
            reason = str(e)
    click.echo(
        click.style("Warning: ", fg="yellow")
        + f"cannot read lease {name} ({reason}). These are the states at the start of the shell.",
        err=True,
    )
    return _from_snapshot(name, snapshot)


@click.group(name="j-forward")
def j_forward():
    """
    The forwards of the lease of this shell

    A forward connects a port on one member of a lease to a port on a
    different member. The data goes between the exporters through the
    router: this command only shows the forwards.
    """


@j_forward.command(name="status")
@opt_config(exporter=False, allow_missing=True)
@click.option(
    "--lease",
    "lease_name",
    default=None,
    metavar="NAME",
    help="The lease to show. Defaults to the lease of this shell (JMP_LEASE).",
)
@opt_output
@handle_exceptions
def status(config, lease_name: str | None, output: OutputType):
    """
    Show each forward of the lease and its state

    An arrow points from the 'provides' port to each 'requires' port, and a
    double arrow connects the two ends of a path. The states come from the
    lease status:

    \b
      Pending     the controller did not complete the setup
      Listening   the setup is complete, and no connection is open
      Connected   a minimum of one connection is open
      Disabled    an optional member is left out, and the forward cannot operate without it
      Failed      the validation or the setup failed, or a necessary member is lost
    """
    shell_lease = os.environ.get(JMP_LEASE) or None
    name = lease_name or shell_lease
    if not name:
        raise click.UsageError(
            "j-forward must be used inside a jmp shell, or with --lease NAME: JMP_LEASE is not set"
        )

    forwards = forward_status_list(config, name, in_shell=name == shell_lease)

    if not output and not forwards.forwards:
        click.echo(f"Lease {name} has no forwards.")
        return
    model_print(forwards, output)


if __name__ == "__main__":
    j_forward()
