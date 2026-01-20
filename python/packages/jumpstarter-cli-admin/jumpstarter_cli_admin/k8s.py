import json

import click
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException


def handle_k8s_api_exception(e: ApiException):
    """Handle a Kubernetes API exception"""
    # Try to parse the JSON response
    try:
        json_body = json.loads(e.body)
        raise click.ClickException(f"Error from server ({json_body['reason']}): {json_body['message']}") from e
    except json.decoder.JSONDecodeError:
        raise click.ClickException(f"Server error: {e.body}") from e


def handle_k8s_config_exception(e: ConfigException):
    """Handle a Kubernetes config exception"""
    # Try to parse the JSON response
    raise click.ClickException(e.args[0]) from e
