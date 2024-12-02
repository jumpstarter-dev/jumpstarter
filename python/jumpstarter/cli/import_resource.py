import base64
from typing import Optional

import click
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Drivers,
    UserConfigV1Alpha1,
)
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.k8s import ClientsV1Alpha1Api, ExportersV1Alpha1Api

from .util import handle_k8s_api_exception, opt_context, opt_kubeconfig, opt_namespace


@click.group("import")
def import_resource():
    """Import configs from a Kubernetes clsuter"""


@import_resource.command("client")
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
    prompt="Enter a comma-separated list of allowed driver packages (optional)",
    default="",
)
@click.option("--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).")
def import_client(
    name: str,
    namespace: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    allow: str,
    unsafe: bool,
    out: Optional[str],
):
    """Import a client config from a Kubernetes cluster"""
    config.load_kube_config(config_file=kubeconfig, context=context)
    clients_api = ClientsV1Alpha1Api()
    core_api = client.CoreV1Api()
    # Check that a client config with the same name does not exist
    if out is None and ClientConfigV1Alpha1.exists(name):
        raise click.ClickException(f"A client with the name '{name}' already exists.")

    # Try to get the client
    try:
        # Get the client and token secret
        result = clients_api.get_namespaced_client(namespace, name)
        secret = core_api.read_namespaced_secret(result["status"]["credential"]["name"], namespace)
        endpoint = result["status"]["endpoint"]
        token = base64.b64decode(secret.data["token"]).decode("utf8")
        # Create the client config and save it
        allow_drivers = allow.split(",") if len(allow) > 0 else []
        client_config = ClientConfigV1Alpha1(
            name=name,
            endpoint=endpoint,
            token=token,
            drivers=ClientConfigV1Alpha1Drivers(allow=allow_drivers, unsafe=unsafe),
        )
        ClientConfigV1Alpha1.save(client_config, out)

        # If this is the only client config, set it as default
        if out is None and len(ClientConfigV1Alpha1.list()) == 1:
            user_config = UserConfigV1Alpha1.load_or_create()
            user_config.config.current_client = client_config
            UserConfigV1Alpha1.save(user_config)

        click.echo(f"Client configuration successfully saved to {client_config.path}")
    except ApiException as e:
        handle_k8s_api_exception(e)

@import_resource.command("exporter")
@click.argument("name", default="default")
@opt_namespace
@opt_kubeconfig
@opt_context
def create(
    name: str,
    namespace: str,
    kubeconfig: Optional[str],
    context: Optional[str]
):
    """Import an exporter config from a Kubernetes cluster"""
    config.load_kube_config(config_file=kubeconfig, context=context)
    exporters_api = ExportersV1Alpha1Api()
    core_api = client.CoreV1Api()

    try:
        ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError:
        pass
    else:
        raise click.ClickException(f'An exporter with the name "{name}" already exists')

     # Try to get the exporter
    try:
        # Get the exporter and token secret
        result = exporters_api.get_namespaced_exporter(namespace, name)
        secret = core_api.read_namespaced_secret(result["status"]["credential"]["name"], namespace)
        endpoint = result["status"]["endpoint"]
        token = base64.b64decode(secret.data["token"]).decode("utf8")
        # Create the exporter config and save it
        exporter_config = ExporterConfigV1Alpha1(
            alias=name,
            endpoint=endpoint,
            token=token,
        )
        exporter_config.save()
        click.echo(f"Exporter configuration successfully saved to {exporter_config.path}")
    except ApiException as e:
        handle_k8s_api_exception(e)
