import logging
from typing import Optional

import asyncclick as click
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from jumpstarter.config import ClientConfigV1Alpha1, UserConfigV1Alpha1
from jumpstarter.k8s import ClientsV1Alpha1Api

from .util import (
    AliasedGroup,
    handle_k8s_api_exception,
    handle_k8s_config_exception,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
    opt_namespace,
)


@click.group(cls=AliasedGroup)
@opt_log_level
def create(log_level: Optional[str]):
    """Create Jumpstarter Kubernetes objects"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


@create.command("client")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--save",
    "-s",
    help="Save the config file for the created client.",
    is_flag=True,
    default=False,
)
@click.option(
    "-a",
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    default=None,
)
@click.option("--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).")
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the client config.",
    default=None,
)
@opt_namespace
@opt_kubeconfig
@opt_context
async def create_client(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str,
    save: bool,
    allow: Optional[str],
    unsafe: bool,
    out: Optional[str],
):
    """Create a client object in the Kubernetes cluster"""
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            click.echo(f"Creating client \"{name}\" in namespace \"{namespace}\"")
            await api.create_client(name)
            # Save the client config
            if save or out is not None or click.confirm("Save client configuration?"):
                click.echo("Fetching client credentials from cluster")
                client_config = await api.get_client_config(name, allow=[], unsafe=unsafe)
                if unsafe is False:
                    unsafe = click.confirm("Allow unsafe driver client imports?")
                if unsafe is False and allow is None:
                    allow = click.prompt(
                        "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                    )
                allow_drivers = allow.split(",") if allow is not None and len(allow) > 0 else []
                client_config.drivers.unsafe = unsafe
                client_config.drivers.allow = allow_drivers
                ClientConfigV1Alpha1.save(client_config, out)
                # If this is the only client config, set it as default
                if out is None and len(ClientConfigV1Alpha1.list()) == 1:
                    user_config = UserConfigV1Alpha1.load_or_create()
                    user_config.config.current_client = client_config
                    UserConfigV1Alpha1.save(user_config)
                click.echo(f"Client configuration successfully saved to {client_config.path}")
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
