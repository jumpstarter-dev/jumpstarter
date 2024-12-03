from typing import Optional

import asyncclick as click
from pyhelm3 import Client

from .util import opt_context, opt_kubeconfig, opt_namespace


@click.command
@opt_namespace
@opt_kubeconfig
@opt_context
def install(namespace: str, kubeconfig: Optional[str], context: Optional[str]):
    """Install the Jumpstarter service in a Kubernetes cluster"""
    client = Client()