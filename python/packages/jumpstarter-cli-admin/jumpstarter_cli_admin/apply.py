from typing import Optional

import click
import yaml
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import (
    OutputType,
    opt_context,
    opt_kubeconfig,
    opt_namespace,
    opt_output_all,
)
from jumpstarter_cli_common.print import model_print
from jumpstarter_kubernetes import DriverInterfacesV1Alpha1Api, ExporterClassesV1Alpha1Api
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)


@click.group(cls=AliasedGroup)
def apply():
    """Apply Jumpstarter Kubernetes objects from YAML files"""


@apply.command("driverinterface")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def apply_driverinterface(
    file: str, kubeconfig: Optional[str], context: Optional[str], namespace: str, output: OutputType
):
    """Apply a DriverInterface YAML to the cluster"""
    try:
        with open(file) as f:
            body = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise click.ClickException(f"Invalid YAML: {e}") from e

    # Validate the kind
    kind = body.get("kind", "")
    if kind != "DriverInterface":
        raise click.ClickException(f"Expected kind 'DriverInterface', got '{kind}'")

    # Ensure namespace is set
    body.setdefault("metadata", {})
    body["metadata"].setdefault("namespace", namespace)

    try:
        async with DriverInterfacesV1Alpha1Api(namespace, kubeconfig, context) as api:
            result = await api.apply_driver_interface(body)
            if output is None:
                click.echo(f"DriverInterface '{result.metadata.name}' applied in namespace '{namespace}'")
            model_print(result, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@apply.command("exporterclass")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, resolve_path=True))
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def apply_exporterclass(
    file: str, kubeconfig: Optional[str], context: Optional[str], namespace: str, output: OutputType
):
    """Apply an ExporterClass YAML to the cluster"""
    try:
        with open(file) as f:
            body = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise click.ClickException(f"Invalid YAML: {e}") from e

    # Validate the kind
    kind = body.get("kind", "")
    if kind != "ExporterClass":
        raise click.ClickException(f"Expected kind 'ExporterClass', got '{kind}'")

    # Ensure namespace is set
    body.setdefault("metadata", {})
    body["metadata"].setdefault("namespace", namespace)

    try:
        async with ExporterClassesV1Alpha1Api(namespace, kubeconfig, context) as api:
            result = await api.apply_exporter_class(body)
            if output is None:
                click.echo(f"ExporterClass '{result.metadata.name}' applied in namespace '{namespace}'")
            model_print(result, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
