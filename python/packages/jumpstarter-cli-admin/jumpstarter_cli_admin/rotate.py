from typing import Optional

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import (
    NameOutputType,
    opt_context,
    opt_kubeconfig,
    opt_namespace,
    opt_output_name_only,
    validate_name,
)
from jumpstarter_kubernetes import ClientsV1Alpha1Api
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)
from jumpstarter.config.client import ClientConfigV1Alpha1


@click.group(cls=AliasedGroup)
def rotate():
    """Rotate credentials for Jumpstarter Kubernetes objects"""


@rotate.command("client")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--save",
    "-s",
    help="Save the updated config file for the client.",
    is_flag=True,
    default=False,
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the client config.",
    default=None,
)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_name_only
@blocking
async def rotate_client(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str,
    save: bool,
    out: Optional[str],
    output: NameOutputType,
):
    """Rotate the internal token for a client object"""
    validate_name(name)
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if output is None:
                click.echo(f"Rotating token for client '{name}' in namespace '{namespace}'")
            new_token = await api.rotate_client_token(name)
            if output is None:
                click.echo(f"Token rotated for client '{name}'")

            if save or out is not None:
                if ClientConfigV1Alpha1.exists(name):
                    config = ClientConfigV1Alpha1.load(name)
                    config.token = new_token
                    ClientConfigV1Alpha1.save(config, out)
                else:
                    client_config = await api.get_client_config(name, allow=[], unsafe=False)
                    client_config.token = new_token
                    ClientConfigV1Alpha1.save(client_config, out)
                if output is None:
                    click.echo("Client configuration updated with new token")
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
    except Exception as e:
        raise click.ClickException(str(e)) from e
