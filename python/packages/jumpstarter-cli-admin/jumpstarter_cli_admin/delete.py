import logging
from typing import Optional

import asyncclick as click
from jumpstarter.config import ClientConfigV1Alpha1, ExporterConfigV1Alpha1, UserConfigV1Alpha1
from jumpstarter_cli_common import (
    AliasedGroup,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
    opt_namespace,
)
from jumpstarter_kubernetes import ClientsV1Alpha1Api, ExportersV1Alpha1Api
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)


@click.group(cls=AliasedGroup)
@opt_log_level
def delete(log_level: Optional[str]):
    """Create Jumpstarter Kubernetes objects"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


@delete.command("client")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--delete",
    "-d",
    help="Delete the config file for the client.",
    is_flag=True,
    default=False,
)
@opt_namespace
@opt_kubeconfig
@opt_context
async def delete_client(
    name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str, delete: bool
):
    """Delete a client object in the Kubernetes cluster"""
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            await api.delete_client(name)
            click.echo(f"Deleted client '{name}' in namespace '{namespace}'")
            # Save the client config
            if ClientConfigV1Alpha1.exists(name) and (delete or click.confirm("Delete client configuration?")):
                # If this is the default, clear default
                user_config = UserConfigV1Alpha1.load_or_create()
                if user_config.config.current_client is not None and user_config.config.current_client.name == name:
                    user_config.config.current_client = None
                    UserConfigV1Alpha1.save(user_config)
                # Delete the client config
                ClientConfigV1Alpha1.delete(name)
                click.echo("Client configuration successfully deleted")
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@delete.command("exporter")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--delete",
    "-d",
    help="Delete the config file for the exporter.",
    is_flag=True,
    default=False,
)
@opt_namespace
@opt_kubeconfig
@opt_context
async def delete_exporter(
    name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str, delete: bool
):
    """Delete an exporter object in the Kubernetes cluster"""
    try:
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            await api.delete_exporter(name)
            click.echo(f"Deleted exporter '{name}' in namespace '{namespace}'")
            # Save the exporter config
            if ExporterConfigV1Alpha1.exists(name) and (delete or click.confirm("Delete exporter configuration?")):
                # Delete the exporter config
                ExporterConfigV1Alpha1.delete(name)
                click.echo("Exporter configuration successfully deleted")
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
