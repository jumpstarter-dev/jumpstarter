from typing import Optional

import asyncclick as click
from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter_cli_common import (
    opt_context,
    opt_kubeconfig,
    opt_namespace,
)
from jumpstarter_kubernetes import ClientsV1Alpha1Api, ExportersV1Alpha1Api
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)


@click.group("import")
def import_res():
    """Import configs from a Kubernetes cluster"""


@import_res.command("client")
@click.argument("name", type=str)
@opt_namespace
@opt_kubeconfig
@opt_context
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the client config.",
)
@click.option(
    "-a",
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    default=None,
)
@click.option("--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).")
async def import_client(
    name: str,
    namespace: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    allow: Optional[str],
    unsafe: bool,
    out: Optional[str],
):
    """Import a client config from a Kubernetes cluster"""
    # Check that a client config with the same name does not exist
    if out is None and ClientConfigV1Alpha1.exists(name):
        raise click.ClickException(f"A client with the name '{name}' already exists")
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if unsafe is False and allow is None:
                unsafe = click.confirm("Allow unsafe driver client imports?")
                if unsafe is False:
                    allow = click.prompt(
                        "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                    )
            click.echo("Fetching client credentials from cluster")
            allow_drivers = allow.split(",") if allow is not None and len(allow) > 0 else []
            client_config = await api.get_client_config(name, allow=allow_drivers, unsafe=unsafe)
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


@import_res.command("exporter")
@click.argument("name", default="default")
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the exporter config.",
)
@opt_namespace
@opt_kubeconfig
@opt_context
async def import_exporter(
    name: str, namespace: str, out: Optional[str], kubeconfig: Optional[str], context: Optional[str]
):
    """Import an exporter config from a Kubernetes cluster"""
    try:
        ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError:
        pass
    else:
        raise click.ClickException(f'An exporter with the name "{name}" already exists')
    try:
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            click.echo("Fetching exporter credentials from cluster")
            exporter_config = await api.get_exporter_config(name)
            ExporterConfigV1Alpha1.save(exporter_config, out)
            click.echo(f"Exporter configuration successfully saved to {exporter_config.path}")
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)