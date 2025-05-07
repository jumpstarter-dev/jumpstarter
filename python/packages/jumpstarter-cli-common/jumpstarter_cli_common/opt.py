import logging
from typing import Literal, Optional

import click


def _opt_log_level_callback(ctx, param, value):
    if value:
        logging.basicConfig(level=value.upper())
    else:
        logging.basicConfig(level=logging.INFO)


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

opt_labels = click.option("-l", "--label", "labels", type=(str, str), multiple=True, help="Labels")


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
