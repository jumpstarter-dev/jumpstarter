from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.config import (
    ClientConfigListV1Alpha1,
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Drivers,
    ObjectMeta,
    UserConfigV1Alpha1,
)


@click.command("create-config", short_help="Create a client config.")
@click.argument("alias")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the client config.",
)
@click.option(
    "--namespace",
    type=str,
    help="Enter the Jumpstarter client namespace.",
    prompt="Enter a valid Jumpstarter client nespace",
)
@click.option(
    "--name",
    type=str,
    help="Enter the Jumpstarter client name.",
    prompt="Enter a valid Jumpstarter client name",
)
@click.option(
    "-e",
    "--endpoint",
    type=str,
    help="Enter the Jumpstarter service endpoint.",
    prompt="Enter a valid Jumpstarter service endpoint",
)
@click.option(
    "-t",
    "--token",
    type=str,
    help="A valid Jumpstarter auth token generated by the Jumpstarter service.",
    prompt="Enter a Jumpstarter auth token (hidden)",
    hide_input=True,
)
@click.option(
    "-a",
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    prompt="Enter a comma-separated list of allowed driver packages (optional)",
    default="",
)
@click.option("--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).")
@handle_exceptions
def create_client_config(
    alias: str,
    namespace: str,
    name: str,
    endpoint: str,
    token: str,
    allow: str,
    unsafe: bool,
    out: Optional[str],
):
    """Create a Jumpstarter client configuration."""
    if out is None and ClientConfigV1Alpha1.exists(alias):
        raise click.ClickException(f"A client with the name '{alias}' already exists.")

    config = ClientConfigV1Alpha1(
        alias=alias,
        metadata=ObjectMeta(namespace=namespace, name=name),
        endpoint=endpoint,
        token=token,
        drivers=ClientConfigV1Alpha1Drivers(allow=allow.split(","), unsafe=unsafe),
    )
    ClientConfigV1Alpha1.save(config, out)

    # If this is the only client config, set it as default
    if out is None and len(ClientConfigV1Alpha1.list()) == 1:
        user_config = UserConfigV1Alpha1.load_or_create()
        user_config.config.current_client = config
        UserConfigV1Alpha1.save(user_config)


def set_next_client(name: str):
    user_config = UserConfigV1Alpha1.load() if UserConfigV1Alpha1.exists() else None
    if (
        user_config is not None
        and user_config.config.current_client is not None
        and user_config.config.current_client.alias == name
    ):
        for c in ClientConfigV1Alpha1.list():
            if c.alias != name:
                # Use the next available client config
                user_config.use_client(c.alias)
                return
        # Otherwise, set client to none
        user_config.use_client(None)


@click.command("delete-config", short_help="Delete a client config.")
@click.argument("name", type=str)
@handle_exceptions
def delete_client_config(name: str):
    """Delete a Jumpstarter client configuration."""
    set_next_client(name)
    ClientConfigV1Alpha1.delete(name)


@click.command("list-configs", short_help="List available client configurations.")
@opt_output_all
@handle_exceptions
def list_client_configs(output: OutputType):
    # Allow listing if there is no user config defined
    current_name = None
    if UserConfigV1Alpha1.exists():
        current_client = UserConfigV1Alpha1.load().config.current_client
        current_name = current_client.alias if current_client is not None else None

    configs = ClientConfigV1Alpha1.list()

    if output == OutputMode.JSON:
        click.echo(ClientConfigListV1Alpha1(current_config=current_name, items=configs).dump_json())
    elif output == OutputMode.YAML:
        click.echo(ClientConfigListV1Alpha1(current_config=current_name, items=configs).dump_yaml())
    else:
        columns = ["CURRENT", "NAME", "ENDPOINT", "PATH"]

        def make_row(c: ClientConfigV1Alpha1):
            return {
                "CURRENT": "*" if current_name == c.alias else "",
                "NAME": c.alias,
                "ENDPOINT": c.endpoint,
                "PATH": str(c.path),
            }

        rows = list(map(make_row, configs))
        click.echo(make_table(columns, rows))


@click.command("use-config", short_help="Select the current client config.")
@click.argument("name", type=str)
@handle_exceptions
def use_client_config(name: str):
    """Select the current Jumpstarter client configuration to use."""
    user_config = UserConfigV1Alpha1.load_or_create()
    user_config.use_client(name)
