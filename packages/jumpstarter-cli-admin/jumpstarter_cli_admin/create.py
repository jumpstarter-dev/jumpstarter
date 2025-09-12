from typing import Optional

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import (
    OutputType,
    confirm_insecure_tls,
    opt_context,
    opt_insecure_tls_config,
    opt_kubeconfig,
    opt_labels,
    opt_namespace,
    opt_nointeractive,
    opt_output_all,
)
from jumpstarter_cli_common.print import model_print
from jumpstarter_kubernetes import ClientsV1Alpha1Api, ExportersV1Alpha1Api
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .cluster import _validate_cluster_type, create_cluster_only
from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1

opt_oidc_username = click.option("--oidc-username", "oidc_username", type=str, default=None, help="OIDC username")


@click.group(cls=AliasedGroup)
def create():
    """Create Jumpstarter Kubernetes objects"""


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
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the client config.",
    default=None,
)
@opt_namespace
@opt_labels()
@opt_kubeconfig
@opt_context
@opt_insecure_tls_config
@opt_oidc_username
@opt_nointeractive
@opt_output_all
@blocking
async def create_client(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    insecure_tls_config: bool,
    namespace: str,
    labels: dict[str, str],
    save: bool,
    allow: Optional[str],
    unsafe: bool,
    out: Optional[str],
    oidc_username: str | None,
    nointeractive: bool,
    output: OutputType,
):
    """Create a client object in the Kubernetes cluster"""
    try:
        confirm_insecure_tls(insecure_tls_config, nointeractive)
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if output is None:
                # Only print status if  is not JSON/YAML
                click.echo(f"Creating client '{name}' in namespace '{namespace}'")
            created_client = await api.create_client(name, labels, oidc_username)
            # Save the client config
            if save or out is not None or nointeractive is False and click.confirm("Save client configuration?"):
                if output is None:
                    click.echo("Fetching client credentials from cluster")
                client_config = await api.get_client_config(name, allow=[], unsafe=unsafe)
                if unsafe is False and allow is None:
                    unsafe = click.confirm("Allow unsafe driver client imports?")
                    if unsafe is False:
                        allow = click.prompt(
                            "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                        )
                allow_drivers = allow.split(",") if allow is not None and len(allow) > 0 else []
                client_config.drivers.unsafe = unsafe
                client_config.drivers.allow = allow_drivers
                client_config.tls.insecure = insecure_tls_config
                ClientConfigV1Alpha1.save(client_config, out)
                # If this is the only client config, set it as default
                if out is None and len(ClientConfigV1Alpha1.list().items) == 1:
                    user_config = UserConfigV1Alpha1.load_or_create()
                    user_config.config.current_client = client_config
                    UserConfigV1Alpha1.save(user_config)
                if output is None:
                    click.echo(f"Client configuration successfully saved to {client_config.path}")
            model_print(created_client, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@create.command("exporter")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--save",
    "-s",
    help="Save the config file for the created exporter.",
    is_flag=True,
    default=False,
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, resolve_path=True, writable=True),
    help="Specify an output file for the exporter config.",
    default=None,
)
@opt_namespace
@opt_labels(required=True)
@opt_kubeconfig
@opt_context
@opt_insecure_tls_config
@opt_oidc_username
@opt_nointeractive
@opt_output_all
@blocking
async def create_exporter(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    insecure_tls_config: bool,
    namespace: str,
    labels: dict[str, str],
    save: bool,
    out: Optional[str],
    oidc_username: str | None,
    nointeractive: bool,
    output: OutputType,
):
    """Create an exporter object in the Kubernetes cluster"""
    try:
        confirm_insecure_tls(insecure_tls_config, nointeractive)
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            if output is None:
                click.echo(f"Creating exporter '{name}' in namespace '{namespace}'")
            created_exporter = await api.create_exporter(name, labels, oidc_username)
            # Save the client config
            if save or out is not None or nointeractive is False and click.confirm("Save exporter configuration?"):
                if output is None:
                    click.echo("Fetching exporter credentials from cluster")
                exporter_config = await api.get_exporter_config(name)
                exporter_config.tls.insecure = insecure_tls_config
                ExporterConfigV1Alpha1.save(exporter_config, out)
                if output is None:
                    click.echo(f"Exporter configuration successfully saved to {exporter_config.path}")
            model_print(created_exporter, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@create.command("cluster")
@click.argument("name", type=str, required=False, default="jumpstarter-lab")
@click.option("--kind", is_flag=False, flag_value="kind", default=None, help="Create a local Kind cluster")
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Create a local Minikube cluster",
)
@click.option(
    "--force-recreate",
    is_flag=True,
    help="Force recreate the cluster if it already exists (WARNING: This will destroy all data in the cluster)",
)
@click.option("--kind-extra-args", type=str, help="Extra arguments for the Kind cluster creation", default="")
@click.option("--minikube-extra-args", type=str, help="Extra arguments for the Minikube cluster creation", default="")
@click.option(
    "--custom-certs",
    type=click.Path(exists=True, readable=True, dir_okay=False, resolve_path=True),
    help="Path to custom CA certificate bundle file to inject into the cluster",
)
@opt_nointeractive
@opt_output_all
@blocking
async def create_cluster(
    name: str,
    kind: Optional[str],
    minikube: Optional[str],
    force_recreate: bool,
    kind_extra_args: str,
    minikube_extra_args: str,
    custom_certs: Optional[str],
    nointeractive: bool,
    output: OutputType,
):
    """Create a Kubernetes cluster for running Jumpstarter"""
    cluster_type = _validate_cluster_type(kind, minikube)

    if output is None:
        if kind is None and minikube is None:
            click.echo(f"Auto-detected {cluster_type} as the cluster type")
        click.echo(f'Creating {cluster_type} cluster "{name}"...')

    await create_cluster_only(
        cluster_type,
        force_recreate,
        name,
        kind_extra_args,
        minikube_extra_args,
        kind or "kind",
        minikube or "minikube",
        custom_certs,
    )

    if output is None:
        click.echo(f'Cluster "{name}" is ready for Jumpstarter installation.')
