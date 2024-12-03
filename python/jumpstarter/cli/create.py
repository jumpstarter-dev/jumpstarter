import logging
from typing import Optional

import asyncclick as click
from kubernetes_asyncio import config
from kubernetes_asyncio.client.exceptions import ApiException

from jumpstarter.k8s import ClientsV1Alpha1Api, ExportersV1Alpha1Api, LeasesV1Alpha1Api

from .util import (
    AliasedGroup,
    handle_k8s_api_exception,
    make_table,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
    opt_namespace,
    time_since,
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
@click.option("--save", "-s", is_flag=True, default=False)
@opt_namespace
@opt_kubeconfig
@opt_context
async def create_client(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str,
    save: bool
):
    """Create a client object in the Kubernetes cluster"""
    async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
        try:
            await api.create_client(name)
        except ApiException as e:
            handle_k8s_api_exception(e)
