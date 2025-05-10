import logging
from functools import partial
from typing import Literal, Optional

import click
from rich import traceback
from rich.logging import RichHandler


def _opt_log_level_callback(ctx, param, value):
    traceback.install()

    basicConfig = partial(logging.basicConfig, handlers=[RichHandler()])
    if value:
        basicConfig(level=value.upper())
    else:
        basicConfig(level=logging.INFO)


opt_log_level = click.option(
    "--log-level",
    "log_level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    help="Set the log level",
    expose_value=False,
    callback=_opt_log_level_callback,
)


opt_kubeconfig = click.option(
    "--kubeconfig", "kubeconfig", type=click.File(), default=None, help="path to the kubeconfig file"
)

opt_context = click.option("--context", "context", type=str, default=None, help="Kubernetes context to use")

opt_namespace = click.option("-n", "--namespace", type=str, help="Kubernetes namespace to use", default="default")


def _opt_labels_callback(ctx, param, value):
    labels = {}

    for label in value:
        k, sep, v = label.partition("=")
        if sep == "":
            raise click.BadParameter("Invalid label '{}', should be formatted as 'key=value'".format(k))
        labels[k] = v

    return labels


opt_labels = partial(
    click.option,
    "-l",
    "--label",
    "labels",
    type=str,
    multiple=True,
    help="Labels to set on resource, can be set multiple times",
    callback=_opt_labels_callback,
)

opt_insecure_tls_config = click.option(
    "--insecure-tls-config",
    "insecure_tls_config",
    is_flag=True,
    default=False,
    help="Disable endpoint TLS verification. This is insecure and should only be used for testing purposes",
)


def confirm_insecure_tls(insecure_tls_config: bool, nointeractive: bool):
    """Confirm if insecure TLS config is enabled and user wants to continue.

    Args:
        insecure_tls_config (bool): Insecure TLS config flag requested by the user.
        nointeractive (bool): This flag is set to True if the command is run in non-interactive mode.

    Raises:
        click.Abort: Abort the command if user does not want to continue.
    """
    if nointeractive is False and insecure_tls_config:
        if not click.confirm("Insecure TLS config is enabled. Are you sure you want to continue?"):
            click.echo("Aborting.")
            raise click.Abort()


class OutputMode(str):
    JSON = "json"
    YAML = "yaml"
    NAME = "name"
    PATH = "path"


OutputType = Optional[OutputMode]

opt_output_all = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.JSON, OutputMode.YAML, OutputMode.NAME]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name).',
)

NameOutputType = Optional[Literal["name"]]

opt_output_name_only = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.NAME]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name).',
)

PathOutputType = Optional[Literal["path"]]

opt_output_path_only = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.PATH]),
    default=None,
    help='Output mode. Use "-o path" for shorter output (file/path).',
)

opt_nointeractive = click.option(
    "--nointeractive", is_flag=True, default=False, help="Disable interactive prompts (for use in scripts)."
)
