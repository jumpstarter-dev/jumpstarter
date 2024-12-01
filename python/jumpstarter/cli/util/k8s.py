import json

import click
from kubernetes.client.exceptions import ApiException


def handle_k8s_api_exception(e: ApiException):
    """Handle a Kubernetes API exception"""
    # Try to parse the JSON response
    try:
        json_body = json.loads(e.body)
        raise click.ClickException(f"Error from server ({json_body["reason"]}): {json_body["message"]}") from e
    except json.decoder.JSONDecodeError:
        raise click.ClickException(f"Server error: {e.body}") from e
